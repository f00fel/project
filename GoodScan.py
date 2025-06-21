from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, CallbackQueryHandler
from telegram.ext import filters
from pyzbar.pyzbar import decode
from PIL import Image
import io
import logging
import requests
import json
import re
import hashlib
import asyncio
import aiohttp

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

TOKEN = "8183275871:AAH13XOFrl7a9fe53mDFPEo9HfGju-V9VHw"
OPENROUTER_API_KEY = "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"
SITE_URL = "https://t.me/GoodScan1_bot"
SITE_NAME = "GoodScan"


class RussianBarcodeBot:
    def __init__(self):
        self.application = Application.builder().token(TOKEN).build()
        self.application.add_handler(CommandHandler("start", self.start))
        self.application.add_handler(MessageHandler(filters.PHOTO, self.handle_photo))
        self.application.add_handler(CallbackQueryHandler(self.handle_analysis_button, pattern="^analyze_"))

        self.allergens = [
            'глютен', 'арахис', 'орехи', 'фундук', 'миндаль', 'кешью', 'фисташки',
            'грецкий орех', 'кокос', 'молоко', 'лактоза', 'яйца', 'яичный белок',
            'яичный желток', 'соя', 'горчица', 'сельдерей', 'кунжут', 'сульфиты',
            'моллюски', 'ракообразные', 'рыба', 'пшеница', 'ржа', 'ячмень', 'овёс',
            'шоколад', 'какао', 'кофе', 'цитрусовые', 'клубника', 'персики', 'киви',
            'ананас', 'манго', 'бананы', 'авокадо', 'помидоры', 'баклажаны', 'перец'
        ]

        self.product_cache = {}

    def _generate_product_key(self, product_info):
        data = f"{product_info['name']}_{product_info['brand']}_{product_info['composition']}"
        return hashlib.md5(data.encode()).hexdigest()

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(
            "📷 Отправьте фото штрих-кода для проверки товара в Роскачестве\n\n"
            "Я покажу:\n"
            "- Название товара и производителя\n"
            "- Цену и регион производства\n"
            "- Рейтинг и год исследования\n"
            "- Состав и аллергены\n"
            "- Ссылку на полное исследование"
        )

    async def handle_photo(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            photo_file = await update.message.photo[-1].get_file()
            image_bytes = io.BytesIO()
            await photo_file.download_to_memory(image_bytes)
            image_bytes.seek(0)

            barcode = self.decode_barcode(image_bytes)
            if not barcode:
                await update.message.reply_text("❌ Код не распознан. Попробуйте еще раз.")
                return

            product_info = await self.check_roskachestvo(barcode)
            if product_info:
                product_key = self._generate_product_key(product_info)
                self.product_cache[product_key] = product_info

                await self.send_product_info(update, product_info, product_key)
            else:
                await update.message.reply_text(f"ℹ️ Товар с кодом {barcode} не найден в базе Роскачества")

        except Exception as e:
            logger.error(f"Ошибка: {e}")
            await update.message.reply_text("⚠️ Произошла ошибка при обработке запроса")

    async def handle_analysis_button(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()

        product_key = query.data.split('_')[1]

        product_info = self.product_cache.get(product_key)

        if not product_info:
            await query.edit_message_text(text="❌ Информация о продукте устарела. Пожалуйста, запросите данные снова.")
            return

        await query.edit_message_text(text=f"🔍 Анализируем {product_info['name']} с помощью Qwen3...")

        try:
            analysis_result = await self.analyze_with_qwen(product_info)

            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=f"🤖 *Анализ Qwen3 для {product_info['name']}:*\n\n{analysis_result}",
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"Ошибка при анализе: {e}")
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text="⚠️ Произошла ошибка при анализе продукта"
            )

    def decode_barcode(self, image_bytes):
        try:
            image = Image.open(image_bytes)
            decoded = decode(image)
            return decoded[0].data.decode('utf-8') if decoded else None
        except Exception as e:
            logger.error(f"Ошибка декодирования: {e}")
            return None

    async def check_roskachestvo(self, barcode):
        try:
            url = f"https://rskrf.ru/rest/1/search/barcode?barcode={barcode}"
            response = requests.get(url, timeout=20)

            if response.status_code == 200:
                data = response.json()

                if 'response' not in data or not data['response']:
                    return None

                product_data = data['response']

                return {
                    'name': product_data.get('title', 'Неизвестно'),
                    'brand': product_data.get('manufacturer', 'Неизвестно'),
                    'composition': self._find_info(product_data.get('product_info', []), 'Состав'),
                    'country': self._find_region(product_data.get('product_info', [])),
                    'price': self._parse_price(product_data.get('price', '')),
                    'rating': product_data.get('total_rating', 'Нет данных'),
                    'research_year': self._find_research_year(product_data.get('product_info', [])),
                    'link': product_data.get('product_link', ''),
                    'allergens': self.find_allergens(self._find_info(product_data.get('product_info', []), 'Состав'))
                }
            return None
        except Exception as e:
            logger.error(f"Ошибка API Роскачества: {e}")
            return None

    async def analyze_with_qwen(self, product_info: dict) -> str:
        try:
            prompt = f"""
                Ты — эксперт по качеству продуктов. Проанализируй товар на основе этих данных:
                • Название: {product_info['name']}
                • Производитель: {product_info['brand']}
                • Регион: {product_info['country']}
                • Цена: {product_info['price']}
                • Рейтинг: {product_info['rating']}/5
                • Год исследования: {product_info['research_year']}
                • Состав: {product_info['composition']}
                • Аллергены: {', '.join(product_info['allergens']) or 'не обнаружены'}
                • Ссылка на отчёт: {product_info['link']}

                Задача:  
                - Выдели 3 главных плюса и минуса.  
                - Оцени опасность аллергенов (если есть).  
                - Дай рекомендацию ("Рекомендую/Нет" и почему).  
                Ответ на русском, 5-7 предложений, без технических терминов.
                """

            headers = {
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
                "HTTP-Referer": SITE_URL,
                "X-Title": SITE_NAME
            }

            payload = {
                "model": "qwen/qwen3-14b:free",
                "messages": [
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                "max_tokens": 1000,
                "temperature": 0.4
            }

            async with aiohttp.ClientSession() as session:
                async with session.post(
                        "https://openrouter.ai/api/v1/chat/completions",
                        headers=headers,
                        json=payload,
                        timeout=30
                ) as response:
                    response_data = await response.json()

                    if response.status != 200:
                        error_msg = response_data.get("error", {}).get("message", "Неизвестная ошибка")
                        logger.error(f"Ошибка OpenRouter API: статус {response.status}, сообщение: {error_msg}")
                        return "Не удалось получить анализ: сервис вернул ошибку"

                    return response_data["choices"][0]["message"]["content"]

        except asyncio.TimeoutError:
            logger.error("Таймаут при запросе к OpenRouter API")
            return "Анализ занял слишком много времени, попробуйте позже"
        except Exception as e:
            logger.error(f"Ошибка Qwen3: {e}")
            return "Ошибка при анализе продукта"

    def _find_info(self, product_info, target):
        for item in product_info:
            if item.get('name') == target:
                return item.get('info', 'Не указано')
        return 'Не указано'

    def _find_region(self, product_info):
        return self._find_info(product_info, "Регион")

    def _find_research_year(self, product_info):
        return self._find_info(product_info, "Год исследования")

    def _parse_price(self, price_str):
        if not price_str or not isinstance(price_str, str):
            return 'Цена не указана'

        price_str = (
            price_str.strip()
            .replace('руб', '₽')
            .replace('р.', '₽')
            .replace(' ', '')
        )

        if '₽' in price_str and not price_str.startswith('₽'):
            price_str = price_str.replace('₽', ' ₽')

        return price_str

    def find_allergens(self, composition):
        if not composition or composition.lower() == "состав не указан":
            return []

        found = []
        lower_comp = composition.lower()
        for allergen in self.allergens:
            if re.search(rf'\b{re.escape(allergen)}\b', lower_comp):
                found.append(allergen.capitalize())
        return found

    async def send_product_info(self, update, info, product_key):

        text = (
            "🏅 *Информация от Роскачества*\n\n"
            f"📦 *Товар:* {info['name']}\n"
            f"🏭 *Производитель:* {info['brand']}\n"
            f"🌍 *Регион:* {info['country']}\n"
            f"💰 *Цена:* {info['price']}\n"
            f"⭐ *Рейтинг:* {info['rating']}/5\n"
            f"📅 *Год исследования:* {info['research_year']}\n\n"
            f"🧪 *Состав:* {info['composition']}"
        )

        if info['allergens']:
            text += f"\n⚠️ *Аллергены:* {', '.join(info['allergens'])}"

        text += f"\n🔗 [Подробнее]({info['link']})"

        keyboard = [
            [InlineKeyboardButton("🤖 Проанализировать с Qwen3", callback_data=f"analyze_{product_key}")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=reply_markup)

    def run(self):
        self.application.run_polling()


if __name__ == '__main__':

    bot = RussianBarcodeBot()
    bot.run()
