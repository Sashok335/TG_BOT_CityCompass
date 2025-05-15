import os
import time
import json
import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackContext,CallbackQueryHandler, ConversationHandler

from languages import LANGUAGES
from tokens import TELEGRAM_TOKEN,OPENTRIPMAP_API_KEY

CACHE_FILE = "cache.json"
CACHE_EXPIRY = 86400


SELECTING_LANGUAGE, SHOWING_ATTRACTIONS, SHOWING_OPTIONS = range(3)

cache = {}




def load_cache():
    """Загрузка кэша"""
    global cache
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, 'r', encoding='utf-8') as f:
            try:
                data = json.load(f)
                cache = {k: v for k, v in data.items() if v['expires'] > time.time()}
            except json.JSONDecodeError:
                cache = {}


def save_cache():
    """Сохранение кэша"""
    with open(CACHE_FILE, 'w', encoding='utf-8') as f:
        json.dump({k: v for k, v in cache.items()}, f, ensure_ascii=False)


def cached_request(key, func):
    """Кэширование запросов"""
    if key in cache and cache[key]['expires'] > time.time():
        return cache[key]['data']
    result = func()
    cache[key] = {
        'expires': time.time() + CACHE_EXPIRY,
        'data': result
    }
    save_cache()
    return result


def get_coordinates(city: str):
    """Получение координат через OpenStreetMap"""
    url = 'https://nominatim.openstreetmap.org/search'
    params = {
        'q': city,
        'format': 'json',
        'limit': 1
    }
    headers = {'User-Agent': 'TelegramCityBot/1.0'}
    try:
        response = requests.get(url, params=params, headers=headers, timeout=10)
        response.raise_for_status()
        data = response.json()
        if data:
            return (data[0]['lat'], data[0]['lon'])
    except Exception as e:
        print(f"Geocode error: {e}")
    return (None, None)


def get_wikimedia_images(name: str, city: str, lang: str):
    """Получение фото из Wikimedia Commons"""
    cache_key = f"wikimedia_images_{name}_{city}_{lang}"
    return cached_request(cache_key, lambda: __fetch_wikimedia_images(name, city, lang))


def __fetch_wikimedia_images(name: str, city: str, lang: str):
    url = 'https://commons.wikimedia.org/w/api.php'
    params = {
        'action': 'query',
        'generator': 'search',
        'gsrsearch': f"File:{name} {city}",
        'gsrlimit': 5,
        'prop': 'imageinfo',
        'iiprop': 'url|mime',
        'iiurlwidth': 640,
        'format': 'json',
        'uselang': lang
    }
    try:
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        images = []
        if 'query' in data and 'pages' in data['query']:
            for page in data['query']['pages'].values():
                if 'imageinfo' in page and page['imageinfo'][0]['mime'].startswith('image/'):
                    images.append(page['imageinfo'][0]['url'])
        return images[:3]
    except Exception as e:
        print(f"Wikimedia images error: {e}")
    return []


def get_wikipedia_summary(name: str, lang: str):
    """Получение краткого описания из Wikipedia"""
    cache_key = f"wikipedia_summary_{name}_{lang}"
    return cached_request(cache_key, lambda: __fetch_wikipedia_summary(name, lang))


def __fetch_wikipedia_summary(name: str, lang: str):
    url = f'https://{lang}.wikipedia.org/w/api.php'
    params = {
        'action': 'query',
        'titles': name,
        'prop': 'extracts',
        'exintro': True,
        'explaintext': True,
        'format': 'json',
        'redirects': 1
    }
    try:
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        if 'query' in data and 'pages' in data['query']:
            page = next(iter(data['query']['pages'].values()))
            return page.get('extract', '')[:400] + '...' if 'extract' in page else ''
    except Exception as e:
        print(f"Wikipedia summary error: {e}")
    return ''


def get_wikidata_description(name: str, city: str, lang: str) :
    """Получение описания из Wikidata"""
    cache_key = f"wikidata_desc_{name}_{city}_{lang}"
    return cached_request(cache_key, lambda: __fetch_wikidata_description(name, city, lang))


def __fetch_wikidata_description(name: str, city: str, lang: str):
    """Запрос к Wikidata через SPARQL"""
    url = "https://query.wikidata.org/sparql"
    query = """
    SELECT ?item ?itemLabel ?itemDescription WHERE {{
      ?item rdfs:label "{name}"@{lang}.
      ?item wdt:P31/wdt:P279* wd:Q570116.
      ?item wdt:P131+ ?location.
      ?location rdfs:label "{city}"@{lang}.
      SERVICE wikibase:label {{ bd:serviceParam wikibase:language "{lang}". }}
    }}
    """.format(name=name.replace('"', '\\"'), city=city.replace('"', '\\"'), lang=lang)

    headers = {
        'User-Agent': 'TelegramCityBot/1.0 (Wikidata SPARQL)',
        'Accept': 'application/sparql-results+json'
    }

    try:
        response = requests.get(url, params={'query': query}, headers=headers, timeout=10)
        response.raise_for_status()
        data = response.json()
        if 'results' in data and data['results']['bindings']:
            desc = data['results']['bindings'][0].get('itemDescription', {}).get('value')
            return desc[:400] + '...' if desc else ''
    except Exception as e:
        print(f"Wikidata error: {e}")
    return ''


def get_dbpedia_description(name: str, city: str, lang: str) :
    """Получение описания из DBpedia"""
    cache_key = f"dbpedia_desc_{name}_{city}_{lang}"
    return cached_request(cache_key, lambda: __fetch_dbpedia_description(name, city, lang))


def __fetch_dbpedia_description(name: str, city: str, lang: str) :
    """Запрос к DBpedia через SPARQL"""
    url = "http://dbpedia.org/sparql"
    query = """
    SELECT ?abstract WHERE {{
      ?resource rdfs:label "{name}"@{lang}.
      ?resource dbo:abstract ?abstract.
      ?resource dbo:location ?location.
      ?location rdfs:label "{city}"@{lang}.
      FILTER (LANG(?abstract) = "{lang}")
    }}
    LIMIT 1
    """.format(name=name.replace('"', '\\"'), city=city.replace('"', '\\"'), lang=lang)

    headers = {
        'User-Agent': 'TelegramCityBot/1.0 (DBpedia SPARQL)',
        'Accept': 'application/sparql-results+json'
    }

    try:
        response = requests.get(url, params={'query': query}, headers=headers, timeout=10)
        response.raise_for_status()
        data = response.json()
        if 'results' in data and data['results']['bindings']:
            return data['results']['bindings'][0]['abstract']['value'][:400] + '...'
    except Exception as e:
        print(f"DBpedia error: {e}")
    return ''


def get_attractions_from_opentripmap(lat: float, lon: float, lang: str, radius: int = 10000, offset: int = 0):
    """Получение достопримечательностей через OpenTripMap"""
    url = f"http://api.opentripmap.com/0.1/{lang}/places/radius"
    params = {
        'radius': radius,
        'lat': lat,
        'lon': lon,
        'apikey': OPENTRIPMAP_API_KEY,
        'format': 'json',
        'limit': 50,
        'offset': offset
    }
    try:
        response = requests.get(url, params=params, timeout=30)
        response.raise_for_status()
        attractions = response.json()
        return [a for a in attractions if a.get('name', '').strip()]
    except Exception as e:
        print(f"OpenTripMap error: {e}")
        return []


def format_attraction(attraction: dict, city: str, lang: str) :
    """Локализованное форматирование информации с поддержкой 4 источников"""
    name = attraction.get('name', 'N/A')
    description = attraction.get('desc') or ''


    if not description.strip():
        wiki_summary = get_wikipedia_summary(name, lang)
        if wiki_summary:
            description = wiki_summary
        else:
            wikidata_desc = get_wikidata_description(name, city, lang)
            if wikidata_desc:
                description = wikidata_desc
            else:
                dbpedia_desc = get_dbpedia_description(name, city, lang)
                description = dbpedia_desc if dbpedia_desc else LANGUAGES[lang]['messages']['no_description']

    xid = attraction.get('xid', '')


    kinds = attraction.get('kinds', 'unknown').split(',')
    category_key = kinds[0].replace('_', ' ').lower() if kinds else 'unknown'
    if 'memorial' in kinds:
        category_key = 'memorial'
    elif 'square' in kinds:
        category_key = 'square'
    category = LANGUAGES[lang]['categories'].get(category_key, category_key.title())


    address = attraction.get('address', {}).get('road', LANGUAGES[lang]['messages']['no_description'])
    if address == LANGUAGES[lang]['messages']['no_description']:
        address = get_landmark_address(name, city, lang)


    images = []
    if 'preview' in attraction and attraction['preview'].get('source'):
        images.append(attraction['preview']['source'])
    else:
        images.extend(get_wikimedia_images(name, city, lang))

    return {
        'name': name,
        'description': description,
        'images': images[:3],
        'category': category,
        'address': address,
        'xid': xid
    }


def get_landmark_address(name: str, city: str, lang: str):
    """Получение адреса через Nominatim"""
    cache_key = f"landmark_address_{name}_{city}_{lang}"
    return cached_request(cache_key, lambda: __fetch_landmark_address(name, city, lang))


def __fetch_landmark_address(name: str, city: str, lang: str):
    url = 'https://nominatim.openstreetmap.org/search'
    params = {
        'q': f"{name}, {city}",
        'format': 'json',
        'limit': 1,
        'accept-language': lang
    }
    headers = {'User-Agent': 'TelegramCityBot/1.0'}
    try:
        response = requests.get(url, params=params, headers=headers, timeout=10)
        response.raise_for_status()
        data = response.json()
        if data:
            return data[0]['display_name']
    except Exception as e:
        print(f"Landmark address error: {e}")
    return LANGUAGES[lang]['messages']['no_description']


def start(update: Update, context: CallbackContext):
    """Старт бота"""
    keyboard = [
        [InlineKeyboardButton(f"{lang['emoji']} {lang['name']}", callback_data=code)]
        for code, lang in LANGUAGES.items()
    ]
    update.message.reply_text(
        LANGUAGES['en']['messages']['welcome'],
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return SELECTING_LANGUAGE


def set_language(update: Update, context: CallbackContext):
    """Установка языка"""
    query = update.callback_query
    user_lang = query.data
    if user_lang not in LANGUAGES:
        query.answer("Ошибка языка")
        return SELECTING_LANGUAGE
    previous_city = context.user_data.get('city')
    previous_radius = context.user_data.get('radius', 10000)
    previous_offset = context.user_data.get('offset', 0)
    context.user_data.clear()
    context.user_data['language'] = LANGUAGES[user_lang]
    if previous_city:
        context.user_data.update({
            'city': previous_city,
            'radius': previous_radius,
            'offset': previous_offset
        })
    query.answer()
    query.edit_message_text(
        LANGUAGES[user_lang]['messages']['enter_city'],
        parse_mode='HTML'
    )
    return SHOWING_ATTRACTIONS


def show_attractions(update: Update, context: CallbackContext):
    """Отображение достопримечательностей с проверкой дубликатов"""
    if 'language' not in context.user_data:
        update.message.reply_text("Ошибка: язык не выбран. Нажмите /start для начала.")
        return SELECTING_LANGUAGE

    if 'shown_xids' not in context.user_data:
        context.user_data['shown_xids'] = set()
    lang = context.user_data['language']['code']
    messages = context.user_data['language']['messages']
    if update.callback_query:
        query = update.callback_query
        query.answer()
        context.user_data['radius'] *= 2
        context.user_data['offset'] = context.user_data.get('offset', 0) + 5
        city = context.user_data['city']
        lat = context.user_data['lat']
        lon = context.user_data['lon']
    else:
        city = update.message.text.strip()
        if not city:
            update.message.reply_text(messages['enter_city'])
            return SHOWING_ATTRACTIONS
        context.user_data.pop('shown_xids', None)
        context.user_data['shown_xids'] = set()
        lat, lon = get_coordinates(city)
        if not lat or not lon:
            update.message.reply_text(
                messages['city_not_found'],
                parse_mode='HTML'
            )
            return SHOWING_ATTRACTIONS
        context.user_data.update({
            'city': city,
            'lat': lat,
            'lon': lon,
            'radius': 10000,
            'offset': 0
        })
    attractions = get_attractions_from_opentripmap(
        lat=context.user_data['lat'],
        lon=context.user_data['lon'],
        lang=lang,
        radius=context.user_data['radius'],
        offset=context.user_data.get('offset', 0)
    )
    new_attractions = []
    for attr in attractions:
        xid = attr.get('xid')
        if xid and xid not in context.user_data['shown_xids']:
            new_attractions.append(attr)
            context.user_data['shown_xids'].add(xid)
    if not new_attractions:
        update.effective_message.reply_text(
            messages['no_attractions'].format(city=city),
            parse_mode='HTML'
        )
        return SHOWING_OPTIONS
    sent_count = 0
    for attraction in new_attractions:
        formatted = format_attraction(attraction, city, lang)
        lang_msgs = LANGUAGES[lang]['messages']

        message = [f" <b>{formatted['name']}</b>"]
        message.append(f"{lang_msgs['category']}: {formatted['category']}")
        if formatted['address'] != messages['no_description']:
            message.append(f" {lang_msgs['address']}: {formatted['address']}")
        if formatted['description'] != messages['no_description']:
            message.append(f"\n{formatted['description']}")
        final_message = "\n".join([part for part in message if part.strip()])
        if formatted['images']:
            media_group = []
            for idx, img_url in enumerate(formatted['images']):
                media_group.append(
                    InputMediaPhoto(
                        media=img_url,
                        caption=final_message if idx == 0 else None,
                        parse_mode='HTML'
                    )
                )
            try:
                update.effective_message.reply_media_group(media=media_group)
            except Exception as e:
                print(f"Media send error: {e}")
                update.effective_message.reply_text(
                    final_message,
                    parse_mode='HTML',
                    disable_web_page_preview=False
                )
        else:
            update.effective_message.reply_text(
                final_message,
                parse_mode='HTML',
                disable_web_page_preview=False
            )
        sent_count += 1
        if sent_count >= 5:
            break

    keyboard = [
        [InlineKeyboardButton(messages['new_city_button'], callback_data='new_city')],
        [InlineKeyboardButton(
            messages['more_button'].format(
                city=city,
                radius=context.user_data['radius'] // 1000
            ),
            callback_data='more_attractions'
        )],
        [InlineKeyboardButton(messages['change_lang_button'], callback_data='change_language')]
    ]
    update.effective_message.reply_text(
        messages['what_next'],
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return SHOWING_OPTIONS


def handle_options(update: Update, context: CallbackContext):
    """Обработка кнопок меню"""
    query = update.callback_query
    choice = query.data
    query.answer()
    lang = context.user_data['language']['code']
    messages = context.user_data['language']['messages']
    if choice == 'new_city':
        context.user_data.pop('city', None)
        context.user_data.pop('lat', None)
        context.user_data.pop('lon', None)
        context.user_data.pop('radius', None)
        context.user_data.pop('offset', None)
        context.user_data.pop('shown_xids', None)
        query.edit_message_text(messages['enter_city'])
        return SHOWING_ATTRACTIONS
    elif choice == 'more_attractions':
        query.edit_message_text(
            messages['expanding_radius'].format(city=context.user_data['city'])
        )
        return show_attractions(update, context)
    elif choice == 'change_language':
        current_city = context.user_data.get('city')
        current_radius = context.user_data.get('radius', 10000)
        current_offset = context.user_data.get('offset', 0)
        current_shown_xids = context.user_data.get('shown_xids', set())
        context.user_data.clear()
        context.user_data['language'] = LANGUAGES[lang]
        if current_city:
            context.user_data.update({
                'city': current_city,
                'radius': current_radius,
                'offset': current_offset,
                'shown_xids': current_shown_xids
            })
        keyboard = [
            [InlineKeyboardButton(f"{l['emoji']} {l['name']}", callback_data=code)]
            for code, l in LANGUAGES.items()
        ]
        query.edit_message_text(
            messages['welcome'],
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return SELECTING_LANGUAGE


def error_handler(update: object, context: CallbackContext) -> None:
    """Обработка ошибок"""
    try:
        lang = context.user_data.get('language', LANGUAGES['en'])['code']
        messages = LANGUAGES[lang]['messages']
    except KeyError:
        lang = 'en'
        messages = LANGUAGES[lang]['messages']
    print(f"Произошла ошибка: {context.error}")
    update.effective_message.reply_text(messages['error'])


def main():
    load_cache()
    updater = Updater(
        TELEGRAM_TOKEN,
        use_context=True
    )
    dispatcher = updater.dispatcher
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            SELECTING_LANGUAGE: [CallbackQueryHandler(set_language)],
            SHOWING_ATTRACTIONS: [
                MessageHandler(Filters.text & ~Filters.command, show_attractions),
                CallbackQueryHandler(show_attractions, pattern='^more_attractions$')
            ],
            SHOWING_OPTIONS: [CallbackQueryHandler(handle_options)]
        },
        fallbacks=[
            MessageHandler(Filters.text & ~Filters.command, show_attractions)
        ],
        allow_reentry=True
    )
    dispatcher.add_handler(conv_handler)
    dispatcher.add_error_handler(error_handler)
    updater.start_polling(timeout=30, read_latency=5.0)
    updater.idle()


if __name__ == '__main__':
    main()