from flask import Flask, jsonify, request, render_template, session, redirect, url_for
from urllib.parse import urlparse, urljoin
from functools import wraps
import json
import math
import os
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone

# app.py はプロジェクト直下に置く。
# 実体（templates / static / data）は bousai_app/ 配下にあるので、そこを参照する。
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.join(BASE_DIR, 'bousai_app')

app = Flask(
    __name__,
    template_folder=os.path.join(APP_DIR, 'templates'),
    static_folder=os.path.join(APP_DIR, 'static'),
)
app.secret_key = 'your-secret-key-here'

# 管理者認証情報
ADMIN_CREDENTIALS = {
    'admin': '123'
}

# ────────────────────────────────
# 気象警報・注意報設定
PREFECTURE_CODE = "020000"  # 青森県
AREA_NAME = "青森市"

# 気象庁の市区町村コード（青森市）
AREA_CODE = "0220100"

WARNING_URL = (
    f"https://www.jma.go.jp/bosai/warning/data/r8/{PREFECTURE_CODE}.json"
)

JST = timezone(timedelta(hours=9))

# 警報・注意報のコード一覧
WARNING_CODES = {
    "00": "解除",
    "02": "暴風雪警報",
    "03": "レベル3大雨警報",
    "04": "洪水警報",
    "05": "暴風警報",
    "06": "大雪警報",
    "07": "波浪警報",
    "08": "レベル3高潮警報",
    "09": "レベル3土砂災害警報",
    "10": "レベル2大雨注意報",
    "12": "大雪注意報",
    "13": "風雪注意報",
    "14": "雷注意報",
    "15": "強風注意報",
    "16": "波浪注意報",
    "17": "融雪注意報",
    "18": "洪水注意報",
    "19": "レベル2高潮注意報",
    "20": "濃霧注意報",
    "21": "乾燥注意報",
    "22": "なだれ注意報",
    "23": "低温注意報",
    "24": "霜注意報",
    "25": "着氷注意報",
    "26": "着雪注意報",
    "27": "その他の注意報",
    "29": "レベル2土砂災害注意報",
    "32": "暴風雪特別警報",
    "33": "レベル5大雨特別警報",
    "35": "暴風特別警報",
    "36": "大雪特別警報",
    "37": "波浪特別警報",
    "38": "レベル5高潮特別警報",
    "39": "レベル5土砂災害特別警報",
    "43": "レベル4大雨危険警報",
    "48": "レベル4高潮危険警報",
    "49": "レベル4土砂災害危険警報"
}

# ────────────────────────────────
# サンプルデータの読み込み
DATA_FILE = os.path.join(APP_DIR, 'data', 'shelters.json')
INSTRUCTIONS_FILE = os.path.join(APP_DIR, 'data', 'instructions.json')
DANGER_PLACES_FILE = os.path.join(APP_DIR, 'data', 'danger_places.json')
DANGER_PLACE_TYPES = {
    'flood': '浸水',
    'tsunami': '津波',
    'landslide': '土砂災害',
    'fire': '火災',
    'road_block': '通行止め',
    'other': 'その他'
}
MAX_DANGER_IMAGE_SIZE = 5 * 1024 * 1024
SHELTER_CATEGORIES = ('指定避難所', '指定緊急避難場所', '福祉避難所', 'その他')
SHELTER_FEATURES = {
    'barrier_free': 'バリアフリー',
    'pets': 'ペット同行可',
    'supplies': '備蓄あり'
}

def load_json(path, default):
    """JSONファイルを読み込む（存在しない・壊れている場合は default を返す）"""
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default

shelters = load_json(DATA_FILE, [])
instructions = load_json(INSTRUCTIONS_FILE, [])

def save_instructions(updated_instructions=None):
    """指示ボードのデータをファイルに保存する"""
    try:
        with open(INSTRUCTIONS_FILE, 'w', encoding='utf-8') as f:
            json.dump(
                instructions if updated_instructions is None else updated_instructions,
                f, ensure_ascii=False, indent=2
            )
    except OSError:
        return False
    return True

def save_shelters(updated_shelters):
    """避難所データをファイルに保存する"""
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(updated_shelters, f, ensure_ascii=False, indent=2)

def detect_danger_image_extension(image_data):
    if image_data.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'png'
    if image_data.startswith(b'\xff\xd8\xff'):
        return 'jpg'
    if image_data.startswith(b'RIFF') and image_data[8:12] == b'WEBP':
        return 'webp'
    return None
# ────────────────────────────────

# ────────────────────────────────
# 認証関連の設定とヘルパー関数
def is_safe_url(target):
    """リダイレクト先URLが安全かどうかチェック"""
    ref_url = urlparse(request.host_url)
    test_url = urlparse(urljoin(request.host_url, target))
    return test_url.scheme in ('http', 'https') and ref_url.netloc == test_url.netloc

def login_required(f):
    """認証が必要なページに付けるデコレータ"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            # 現在のURLをnextパラメータとしてログイン画面にリダイレクト
            return redirect(url_for('login', next=request.url))
        return f(*args, **kwargs)
    return decorated_function

def get_japan_time():
    """日本時間（JST）の現在時刻を取得する"""
    return datetime.now(JST).strftime("%Y年%m月%d日 %H:%M")


def format_report_time(iso_str):
    """気象庁の発表時刻（ISO形式）をJSTの表示用文字列に変換する"""
    if not iso_str:
        return "不明"
    try:
        parsed = datetime.fromisoformat(iso_str.replace('Z', '+00:00'))
        if parsed.tzinfo:
            parsed = parsed.astimezone(JST)
        return parsed.strftime("%Y年%m月%d日 %H:%M")
    except ValueError:
        return iso_str


def filter_shelters(district=None):
    """district 指定があれば一致する避難所のみ、なければ全件を返す"""
    return [s for s in shelters if not district or s.get('district') == district]


def is_valid_coordinate(latitude, longitude):
    return (
        isinstance(latitude, (int, float))
        and not isinstance(latitude, bool)
        and isinstance(longitude, (int, float))
        and not isinstance(longitude, bool)
        and math.isfinite(latitude)
        and math.isfinite(longitude)
        and -90 <= latitude <= 90
        and -180 <= longitude <= 180
    )


def calculate_distance_meters(start_latitude, start_longitude, end_latitude, end_longitude):
    earth_radius_meters = 6371000
    latitude_delta = math.radians(end_latitude - start_latitude)
    longitude_delta = math.radians(end_longitude - start_longitude)
    haversine = (
        math.sin(latitude_delta / 2) ** 2
        + math.cos(math.radians(start_latitude))
        * math.cos(math.radians(end_latitude))
        * math.sin(longitude_delta / 2) ** 2
    )
    return earth_radius_meters * 2 * math.asin(math.sqrt(haversine))


def get_active_resident_notices():
    return [
        instruction for instruction in instructions
        if instruction.get('target') == '住民'
        and instruction.get('status') not in ('解除', '完了')
    ]


def parse_area_warnings(warning_data):
    """気象庁の新形式JSONから対象市区町村の発表・継続中の情報を抽出する"""
    if not isinstance(warning_data, list):
        raise ValueError("気象庁の警報・注意報データが新形式の配列ではありません")

    warnings = []
    seen_codes = set()
    report_datetimes = []

    for report in warning_data:
        if not isinstance(report, dict):
            continue

        report_datetime = report.get("reportDatetime")
        if isinstance(report_datetime, str) and report_datetime:
            report_datetimes.append(report_datetime)

        warning = report.get("warning")
        if not isinstance(warning, dict):
            continue

        class20_items = warning.get("class20Items", [])
        if not isinstance(class20_items, list):
            continue

        area = next(
            (
                item for item in class20_items
                if isinstance(item, dict)
                and item.get("areaCode") == AREA_CODE
            ),
            None
        )
        if not area:
            continue

        kinds = area.get("kinds", [])
        if not isinstance(kinds, list):
            continue

        for kind in kinds:
            if not isinstance(kind, dict):
                continue

            status = kind.get("status", "")
            code = kind.get("code", "")
            if status not in ("発表", "継続") or not code or code in seen_codes:
                continue

            warnings.append({
                "name": WARNING_CODES.get(
                    code,
                    f"不明な警報・注意報 (コード: {code})"
                ),
                "code": code,
                "status": status
            })
            seen_codes.add(code)

    latest_report_datetime = max(report_datetimes, default="")
    return warnings, latest_report_datetime


def get_weather_warnings():
    """対象市区町村の警報・注意報を取得する"""
    try:
        # 青森県の新形式（令和8年～）警報・注意報データを取得
        with urllib.request.urlopen(url=WARNING_URL, timeout=10) as res:
            warning_data = json.loads(res.read())

        warnings, report_datetime = parse_area_warnings(warning_data)

        return {
            "area_name": AREA_NAME,
            "warnings": warnings,
            "report_time": format_report_time(report_datetime),
            "last_fetch_time": get_japan_time()
        }

    except Exception:
        return {
            "area_name": AREA_NAME,
            "warnings": [],
            "report_time": "取得失敗",
            "last_fetch_time": get_japan_time(),
            "error": True
        }


# トップページ：templates/index.html を返す（住民向け指示も表示する）
@app.route('/')
def index():
    resident_notices = get_active_resident_notices()
    return render_template('index.html', resident_notices=resident_notices)

# ログインページ
@app.route('/login', methods=['GET', 'POST'])
def login():
    # リダイレクト先を取得（デフォルトは避難所登録画面）
    next_url = request.args.get('next') or request.form.get('next')

    # 安全でないURLの場合はデフォルトページにリダイレクト
    if not next_url or not is_safe_url(next_url):
        next_url = url_for('shelter_register')

    if request.method == 'POST':
        password = request.form.get('password', '').strip()

        # 認証チェック
        username = next(
            (name for name, registered_password in ADMIN_CREDENTIALS.items()
             if registered_password == password),
            None
        )
        if username:
            session['logged_in'] = True
            session['username'] = username
            # ログイン成功後は指定されたページにリダイレクト
            return redirect(next_url)
        return render_template('login.html', error=True, message="パスワードが正しくありません。", next=next_url)

    # ログイン済みの場合は指定されたページにリダイレクト
    if session.get('logged_in'):
        return redirect(next_url)

    return render_template('login.html', next=next_url)

# ログアウト
@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

@app.route('/shelter_register', methods=['GET', 'POST'])
@login_required
def shelter_register():
    form_data = request.form if request.method == 'POST' else {}

    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        description = request.form.get('description', '').strip()
        danger_type = request.form.get('type', '')
        try:
            latitude = float(request.form.get('latitude', ''))
            longitude = float(request.form.get('longitude', ''))
        except ValueError:
            latitude = longitude = float('nan')

        if not name or len(name) > 100:
            message = '名称を1〜100文字で入力してください。'
        elif len(description) > 2000:
            message = '説明は2000文字以内で入力してください。'
        elif danger_type not in DANGER_PLACE_TYPES:
            message = '被害の種類を選択してください。'
        elif not math.isfinite(latitude) or not -90 <= latitude <= 90:
            message = '地図を選択して正しい緯度を指定してください。'
        elif not math.isfinite(longitude) or not -180 <= longitude <= 180:
            message = '地図を選択して正しい経度を指定してください。'
        else:
            message = None

        upload = request.files.get('image')
        image_data = None
        image_extension = None
        if message is None and upload and upload.filename:
            image_data = upload.read(MAX_DANGER_IMAGE_SIZE + 1)
            if len(image_data) > MAX_DANGER_IMAGE_SIZE:
                message = '画像は5MB以下のファイルを選択してください。'
            else:
                image_extension = detect_danger_image_extension(image_data)
                if not image_extension:
                    message = 'PNG、JPEG、WebP形式の画像を選択してください。'

        if message:
            return render_template(
                'shelter_register.html', error=True, message=message,
                danger_types=DANGER_PLACE_TYPES, form_data=form_data
            )

        places = load_json(DANGER_PLACES_FILE, [])
        if not isinstance(places, list):
            places = []
        next_id = max(
            (place.get('id', 0) for place in places
             if isinstance(place, dict) and isinstance(place.get('id'), int)),
            default=0
        ) + 1
        image_name = f'{uuid.uuid4().hex}.{image_extension}' if image_data else ''
        place = {
            'id': next_id,
            'name': name,
            'description': description,
            'type': danger_type,
            'latitude': latitude,
            'longitude': longitude,
            'image': image_name,
            'created_at': get_japan_time()
        }
        image_path = None
        try:
            if image_data:
                image_dir = os.path.join(app.static_folder, 'danger_places')
                os.makedirs(image_dir, exist_ok=True)
                image_path = os.path.join(image_dir, image_name)
                with open(image_path, 'wb') as image_file:
                    image_file.write(image_data)
            with open(DANGER_PLACES_FILE, 'w', encoding='utf-8') as data_file:
                json.dump(places + [place], data_file, ensure_ascii=False, indent=2)
        except OSError:
            if image_path and os.path.exists(image_path):
                try:
                    os.remove(image_path)
                except OSError:
                    pass
            return render_template(
                'shelter_register.html', error=True,
                message='危険場所を保存できませんでした。もう一度お試しください。',
                danger_types=DANGER_PLACE_TYPES, form_data=form_data
            )

        return render_template(
            'shelter_register.html', success=True,
            message='危険場所を登録しました。',
            danger_types=DANGER_PLACE_TYPES, form_data={}
        )

    return render_template(
        'shelter_register.html', danger_types=DANGER_PLACE_TYPES, form_data=form_data
    )


@app.route('/shelter_add', methods=['GET', 'POST'])
@login_required
def shelter_add():
    form_data = request.form if request.method == 'POST' else {}
    selected_features = request.form.getlist('features') if request.method == 'POST' else []
    message = None
    error = None

    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        category = request.form.get('category', '')
        district = request.form.get('district', '').strip()
        address = request.form.get('address', '').strip()
        phone = request.form.get('phone', '').strip()
        hours = request.form.get('hours', '').strip()
        description = request.form.get('description', '').strip()
        try:
            latitude = float(request.form.get('latitude', ''))
            longitude = float(request.form.get('longitude', ''))
        except ValueError:
            latitude = longitude = float('nan')

        capacity_text = request.form.get('capacity', '').strip()
        try:
            capacity = int(capacity_text) if capacity_text else None
        except ValueError:
            capacity = -1

        if not name or len(name) > 100:
            error = '施設名を1〜100文字で入力してください。'
        elif category not in SHELTER_CATEGORIES:
            error = '施設種別を選択してください。'
        elif not district or len(district) > 100:
            error = '地区を1〜100文字で入力してください。'
        elif not address or len(address) > 300:
            error = '住所を1〜300文字で入力してください。'
        elif capacity == -1 or (capacity is not None and not 0 <= capacity <= 1000000):
            error = '収容人数は0〜1000000人で入力してください。'
        elif len(phone) > 50 or len(hours) > 100 or len(description) > 2000:
            error = '電話番号、利用時間、説明の文字数を確認してください。'
        elif not is_valid_coordinate(latitude, longitude):
            error = '地図上で有効な施設位置を指定してください。'
        else:
            duplicate = any(
                shelter.get('name', '').strip() == name
                and shelter.get('district', '').strip() == district
                for shelter in shelters
            )
            if duplicate:
                error = '同じ地区・施設名の避難所がすでに登録されています。'

        if error is None:
            allowed_features = [feature for feature in selected_features if feature in SHELTER_FEATURES]
            next_id = max(
                (shelter.get('id', 0) for shelter in shelters
                 if isinstance(shelter.get('id'), int)
                 and not isinstance(shelter.get('id'), bool)),
                default=0
            ) + 1
            new_shelter = {
                'id': next_id,
                'name': name,
                'category': category,
                'district': district,
                'address': address,
                'capacity': capacity,
                'phone': phone,
                'hours': hours,
                'features': allowed_features,
                'description': description,
                'latitude': latitude,
                'longitude': longitude
            }
            updated_shelters = shelters + [new_shelter]
            try:
                save_shelters(updated_shelters)
                shelters[:] = updated_shelters
                message = f'{name}を登録しました。近隣検索と全施設一覧に反映されました。'
                form_data = {}
                selected_features = []
            except OSError:
                error = '避難所を保存できませんでした。もう一度お試しください。'

    return render_template(
        'shelter_add.html',
        categories=SHELTER_CATEGORIES,
        features=SHELTER_FEATURES,
        form_data=form_data,
        selected_features=selected_features,
        message=message,
        error=error
    )

# 避難所検索ページと位置情報登録
@app.route('/shelter_search', methods=['GET', 'POST'])
def shelter_search():
    message = None
    error = None
    if request.method == 'POST':
        if not session.get('logged_in'):
            return redirect(url_for('login', next=request.url))

        shelter_id = request.form.get('shelter_id', '')
        try:
            latitude = float(request.form.get('latitude', ''))
            longitude = float(request.form.get('longitude', ''))
        except ValueError:
            latitude = longitude = float('nan')

        selected_shelter = next(
            (shelter for shelter in shelters
             if str(shelter.get('id')) == shelter_id),
            None
        )
        if selected_shelter is None:
            error = '避難所を選択してください。'
        elif not is_valid_coordinate(latitude, longitude):
            error = '地図上の位置を選択してください。'
        else:
            updated_shelters = [dict(shelter) for shelter in shelters]
            updated_shelter = next(
                shelter for shelter in updated_shelters
                if str(shelter.get('id')) == shelter_id
            )
            updated_shelter['latitude'] = latitude
            updated_shelter['longitude'] = longitude
            try:
                save_shelters(updated_shelters)
                shelters[:] = updated_shelters
                message = f'{selected_shelter.get("name", "避難所")}の位置を保存しました。'
            except OSError:
                error = '位置情報を保存できませんでした。もう一度お試しください。'

    return render_template(
        'shelter_search.html',
        shelters=shelters,
        message=message,
        error=error,
        logged_in=session.get('logged_in', False)
    )

# 全施設一覧ページ
@app.route('/all_shelters')
def all_shelters():
    return render_template('search_results.html', results=shelters)


# 指示ボード：住民向けの指示を一覧で確認する
@app.route('/board', methods=['GET', 'POST'])
@login_required
def board():
    error = None
    message = None
    form_data = {}

    if request.method == 'POST':
        action = request.form.get('action', '')
        updated_instructions = [
            dict(instruction) for instruction in instructions
            if isinstance(instruction, dict)
        ]

        if action == 'publish':
            content = request.form.get('content', '').strip()
            shelter = request.form.get('shelter', '').strip()
            form_data = {'content': content, 'shelter': shelter}
            if not content or len(content) > 1000:
                error = '発信内容を1〜1000文字で入力してください。'
            elif len(shelter) > 100:
                error = '避難先は100文字以内で入力してください。'
            else:
                next_id = max(
                    (instruction.get('id', 0) for instruction in updated_instructions
                     if isinstance(instruction.get('id'), int)
                     and not isinstance(instruction.get('id'), bool)),
                    default=0
                ) + 1
                now = get_japan_time()
                updated_instructions.insert(0, {
                    'id': next_id,
                    'target': '住民',
                    'content': content,
                    'shelter': shelter,
                    'status': '発信中',
                    'created_at': now,
                    'updated_at': now
                })
                message = '住民への発信を公開しました。'
        elif action == 'resolve':
            instruction_id = request.form.get('instruction_id', '')
            notice = next((
                instruction for instruction in updated_instructions
                if str(instruction.get('id')) == instruction_id
                and instruction.get('target') == '住民'
                and instruction.get('status') not in ('解除', '完了')
            ), None)
            if notice is None:
                error = '解除できる発信が見つかりません。'
            else:
                notice['status'] = '解除'
                notice['updated_at'] = get_japan_time()
                message = '発信を解除しました。'
        else:
            error = '操作を確認できませんでした。'

        if message and save_instructions(updated_instructions):
            instructions[:] = updated_instructions
        elif message:
            error = '発信を保存できませんでした。もう一度お試しください。'
            message = None

    resident_instructions = [
        instruction for instruction in instructions
        if instruction.get('target') == '住民'
    ]
    return render_template(
        'board.html',
        instructions=resident_instructions,
        active_instructions=get_active_resident_notices(),
        resolved_instructions=[
            instruction for instruction in resident_instructions
            if instruction.get('status') in ('解除', '完了')
        ],
        message=message,
        error=error,
        form_data=form_data
    )

# 検索結果ページ：templates/search_results.html を返す
@app.route('/search_results')
def search_results():
    results = filter_shelters(request.args.get('district'))
    return render_template('search_results.html', results=results)

# JSON API：/shelters?district=地区名
@app.route('/shelters', methods=['GET'])
def get_shelters():
    results = filter_shelters(request.args.get('district'))

    if not results:
        # 見つからなければエラー JSON を返す
        return jsonify({'error': 'No shelters found'}), 404

    # 見つかったらリストを JSON で返す
    return jsonify(results)


@app.route('/api/shelters/nearby', methods=['GET'])
def api_nearby_shelters():
    try:
        latitude = float(request.args.get('latitude', ''))
        longitude = float(request.args.get('longitude', ''))
    except ValueError:
        return jsonify({'error': '有効な現在地が必要です。'}), 400
    if not is_valid_coordinate(latitude, longitude):
        return jsonify({'error': '有効な現在地が必要です。'}), 400

    located_shelters = []
    unlocated_count = 0
    for shelter in shelters:
        shelter_latitude = shelter.get('latitude')
        shelter_longitude = shelter.get('longitude')
        if not is_valid_coordinate(shelter_latitude, shelter_longitude):
            unlocated_count += 1
            continue
        distance = calculate_distance_meters(
            latitude, longitude, shelter_latitude, shelter_longitude
        )
        located_shelters.append({
            'id': shelter.get('id'),
            'name': shelter.get('name', '名称未設定'),
            'latitude': shelter_latitude,
            'longitude': shelter_longitude,
            'distance_m': round(distance)
        })

    located_shelters.sort(key=lambda shelter: shelter['distance_m'])
    return jsonify({
        'shelters': located_shelters,
        'unlocated_count': unlocated_count
    })


@app.route('/api/shelters/route', methods=['GET'])
def api_shelter_route():
    coordinate_names = ('origin_lat', 'origin_lon', 'destination_lat', 'destination_lon')
    try:
        origin_latitude, origin_longitude, destination_latitude, destination_longitude = (
            float(request.args.get(name, '')) for name in coordinate_names
        )
    except ValueError:
        return jsonify({'error': '出発地と避難所の位置が必要です。'}), 400

    if not is_valid_coordinate(origin_latitude, origin_longitude) or not is_valid_coordinate(
        destination_latitude, destination_longitude
    ):
        return jsonify({'error': '有効な位置情報が必要です。'}), 400

    route_url = (
        'https://routing.openstreetmap.de/routed-foot/route/v1/driving/'
        f'{origin_longitude},{origin_latitude};{destination_longitude},{destination_latitude}'
        '?overview=full&geometries=geojson&steps=true'
    )
    route_request = urllib.request.Request(
        route_url,
        headers={'User-Agent': 'BousaiApp/1.0 (pedestrian shelter directions)'}
    )
    try:
        with urllib.request.urlopen(route_request, timeout=15) as response:
            route_data = json.loads(response.read())
    except (OSError, json.JSONDecodeError):
        return jsonify({'error': '徒歩ルートを取得できません。時間をおいて再度お試しください。'}), 502

    routes = route_data.get('routes', [])
    if route_data.get('code') != 'Ok' or not routes:
        return jsonify({'error': 'この場所間の徒歩ルートが見つかりません。'}), 404

    route = routes[0]
    legs = route.get('legs', [])
    steps = legs[0].get('steps', []) if legs else []
    return jsonify({
        'distance_m': round(route.get('distance', 0)),
        'duration_s': round(route.get('duration', 0)),
        'geometry': route.get('geometry'),
        'steps': [{
            'type': step.get('maneuver', {}).get('type', 'continue'),
            'modifier': step.get('maneuver', {}).get('modifier', ''),
            'name': step.get('name', ''),
            'distance_m': round(step.get('distance', 0))
        } for step in steps]
    })

# JSON API：地図に表示する危険場所
@app.route('/api/danger_places', methods=['GET'])
def api_danger_places():
    places = load_json(DANGER_PLACES_FILE, [])
    if not isinstance(places, list):
        return jsonify([])

    valid_places = [
        place for place in places
        if isinstance(place, dict)
        and isinstance(place.get('name'), str)
        and place.get('name').strip()
        and isinstance(place.get('latitude'), (int, float))
        and not isinstance(place.get('latitude'), bool)
        and -90 <= place.get('latitude') <= 90
        and isinstance(place.get('longitude'), (int, float))
        and not isinstance(place.get('longitude'), bool)
        and -180 <= place.get('longitude') <= 180
    ]
    for place in valid_places:
        image_name = place.get('image')
        if isinstance(image_name, str) and image_name and os.path.basename(image_name) == image_name:
            place['image_url'] = url_for(
                'static', filename=f'danger_places/{image_name}'
            )
    return jsonify(valid_places)

# 気象警報・注意報API
@app.route('/api/weather_warnings')
def api_weather_warnings():
    """気象警報・注意報をJSON形式で返すAPI"""
    return jsonify(get_weather_warnings())

if __name__ == '__main__':
    app.run(debug=True, port=5000)
