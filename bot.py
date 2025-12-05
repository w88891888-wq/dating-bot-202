from aiogram import Bot, Dispatcher, executor, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ForceReply
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
import asyncio
import os
import logging

# 配置日志
logging.basicConfig(level=logging.INFO)

API_TOKEN = os.getenv('API_TOKEN')
ADMIN_ID = int(os.getenv('ADMIN_ID'))
DATA_CHANNEL = int(os.getenv('DATA_CHANNEL'))
PUBLIC_GROUP = int(os.getenv('PUBLIC_GROUP'))

bot = Bot(token=API_TOKEN, parse_mode="HTML")
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)

# 存储用户提交限制（防刷）
user_submissions = {}

class SubmitData(StatesGroup):
    waiting_for_data = State()

@dp.message_handler(commands=['start'], chat_type=types.ChatType.PRIVATE)
async def start_private(message: types.Message):
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("⚡️ 开始提交交友资料", callback_data="submit_data"))
    await message.answer(
        "✨ 欢迎使用交友资料提交系统\n\n"
        "请按要求提交：\n"
        "1. 一段自我介绍文字（包含城市/年龄/性别/职业/兴趣/微信等）\n"
        "2. 1-10张生活照（可附短视频）\n"
        "提交后会进入审核，审核通过会发布到交友群并带关键词标签",
        reply_markup=kb
    )

@dp.callback_query_handler(lambda c: c.data == "submit_data")
async def process_submit(call: types.CallbackQuery, state: FSMContext):
    user_id = call.from_user.id
    now = asyncio.get_event_loop().time()
    if user_id in user_submissions and now - user_submissions[user_id] < 3600:  # 1小时限1次
        await call.answer("⏰ 1小时内只能提交1次，请稍后再试", show_alert=True)
        return
    user_submissions[user_id] = now
    await call.message.edit_text("请直接发资料给我（文字+照片/视频一次发完），发完后我会自动整理")
    await SubmitData.waiting_for_data.set()

@dp.message_handler(state=SubmitData.waiting_for_data, content_types=types.ContentTypes.ANY)
async def receive_data(message: types.Message, state: FSMContext):
    user = message.from_user
    caption = message.caption or message.text or "无文字"
    
    # 转发到资料库频道
    sent = await message.forward(DATA_CHANNEL)
    
    # 给管理员加按钮
    admin_kb = InlineKeyboardMarkup()
    admin_kb.add(
        InlineKeyboardButton("✅ 发布到交友群", callback_data=f"publish_{sent.message_id}_{user.id}"),
        InlineKeyboardButton("❌ 拒绝", callback_data=f"reject_{sent.message_id}_{user.id}")
    )
    
    await bot.send_message(
        DATA_CHANNEL,
        f"新资料待审核 👤\n"
        f"用户：{user.first_name} ({user.id})\n"
        f"内容如下：",
        reply_to_message_id=sent.message_id,
        reply_markup=admin_kb
    )
    
    await message.reply("✅ 资料已提交，正在等待审核～\n通常1-12小时内会处理")
    await state.finish()

@dp.callback_query_handler(lambda c: c.data.startswith("publish_"))
async def publish_card(call: types.CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        await call.answer("❌ 无权限", show_alert=True)
        return
    _, msg_id, user_id = call.data.split("_")
    msg_id = int(msg_id)
    
    # 复制原资料到公开群
    forwarded = await bot.forward_message(PUBLIC_GROUP, DATA_CHANNEL, msg_id)
    
    # 让管理员输入关键词
    await bot.send_message(
        call.from_user.id,
        f"请为这条资料回复关键词（用空格分开）\n例：北京 25 女 教师 旅游 美食",
        reply_markup=ForceReply()
    )
    
    # 临时保存
    await storage.set_data(chat=call.from_user.id, user=call.from_user.id, data={"pending_msg": forwarded.message_id})

@dp.message_handler(lambda message: message.reply_to_message and "请为这条资料回复关键词" in message.reply_to_message.text)
async def receive_keywords(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    keywords = message.text.strip()
    pending_data = await storage.get_data(chat=message.from_user.id, user=message.from_user.id)
    if not pending_data or "pending_msg" not in pending_data:
        return
    msg_id = pending_data["pending_msg"]
    
    # 添加关键词按钮 + 报错按钮
    kb = InlineKeyboardMarkup(row_width=4)
    keyword_buttons = [InlineKeyboardButton(f"#{k}", callback_data=f"dummy") for k in keywords.split()[:10]]
    kb.add(*keyword_buttons)
    kb.add(InlineKeyboardButton("🚨 报错/举报", callback_data=f"report_{msg_id}"))
    
    await bot.edit_message_reply_markup(PUBLIC_GROUP, msg_id, reply_markup=kb)
    await bot.edit_message_caption(
        PUBLIC_GROUP, msg_id,
        caption=f"关键词：{keywords}\n\n⚠️ 发现信息不实请点击下方举报按钮",
        reply_markup=kb
    )
    await message.reply("✅ 已成功发布并添加关键词！")
    # 通知提交者
    await bot.send_message(user_id, "🎉 你的资料已通过审核并发布到群里！")

@dp.callback_query_handler(lambda c: c.data.startswith("report_"))
async def report_card(call: types.CallbackQuery):
    msg_id = call.data.split("_")[1]
    await bot.forward_message(ADMIN_ID, PUBLIC_GROUP, msg_id)
    await bot.send_message(ADMIN_ID, f"🚨 有人举报了上面的资料，请处理！\n举报者：{call.from_user.first_name} ({call.from_user.id})")
    await call.answer("✅ 已收到举报，管理员会尽快处理", show_alert=True)
    # 可选：自动删除
    # await bot.delete_message(PUBLIC_GROUP, msg_id)

@dp.message_handler(commands=['search'], chat_id=PUBLIC_GROUP)
async def search(message: types.Message):
    keyword = message.text[8:].strip()
    if not keyword:
        await message.reply("用法：/search 北京 女 25")
        return
    await message.reply(f"🔍 正在搜索包含【{keyword}】的资料…（用 Telegram 搜索功能辅助）")

if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True)
