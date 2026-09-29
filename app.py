from flask import Flask, jsonify, request, render_template, session, redirect, url_for
from urllib.parse import urlparse, urljoin, urlencode
from functools import wraps
import hashlib
import json
import math
import mimetypes
import os
import threading
import time
import tempfile
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
HAZARD_REPORTS_FILE = os.path.join(APP_DIR, 'data', 'hazard_reports.json')
HAZARD_SEEN_FILE = os.path.join(APP_DIR, 'data', 'hazard_seen.json')
HAZARD_UPLOAD_DIR = os.path.join(app.static_folder, 'hazard_reports')
DANGER_PLACE_TYPES = {
    'flood': '浸水',
    'tsunami': '津波',
    'landslide': '土砂災害',
    'fire': '火災',
    'road_block': '通行止め',
    'other': 'その他'
}
MAX_DANGER_IMAGE_SIZE = 5 * 1024 * 1024
HAZARD_TYPES = (
    '道路の損傷', '倒木・落下物', '浸水・冠水', '津波', '河川氾濫',
    '道路冠水', '土砂崩れ', '積雪による道路寸断', '獣害', 'その他'
)
HAZARD_UPLOAD_TYPES = {
    '.jpg': ('image', {'image/jpeg'}),
    '.jpeg': ('image', {'image/jpeg'}),
    '.png': ('image', {'image/png'}),
    '.gif': ('image', {'image/gif'}),
    '.webp': ('image', {'image/webp'}),
    '.mp4': ('video', {'video/mp4'}),
    '.webm': ('video', {'video/webm'}),
    '.mov': ('video', {'video/quicktime'}),
    '.m4v': ('video', {'video/x-m4v', 'video/mp4'})
}
MAX_HAZARD_FILES = 5
MAX_HAZARD_FILE_SIZE = 20 * 1024 * 1024
MAX_HAZARD_REQUEST_SIZE = 50 * 1024 * 1024
HAZARD_GEOCODE_LAST_REQUEST_AT = 0.0
HAZARD_GEOCODE_CACHE = {}
HAZARD_STORAGE_LOCK = threading.Lock()
HAZARD_GEOCODE_LOCK = threading.Lock()
SHELTER_CATEGORIES = ('指定避難所', '指定緊急避難場所', '福祉避難所', 'その他')
SHELTER_FEATURES = {
    'barrier_free': 'バリアフリー',
    'pets': 'ペット同行可',
    'supplies': '備蓄あり'
}
INSTRUCTION_TARGETS = ('住民', '防災課')
INSTRUCTION_URGENCIES = ('緊急', '警戒', '通常')
RESIDENT_STATUSES = ('発信中', '対応中', '解除')
AGENCY_STATUSES = ('未対応', '対応中', '完了')
ROUTE_SAFETY_MARGIN_METERS = 75
ROUTE_EXCLUSION_CORRIDOR_METERS = 2000
GEOCODER_LAST_REQUEST_AT = 0.0
GEOCODER_RATE_LIMIT_LOCK = threading.Lock()

app.config['MAX_CONTENT_LENGTH'] = MAX_HAZARD_REQUEST_SIZE

def load_json(path, default):
    """JSONファイルを読み込む（存在しない・壊れている場合は default を返す）"""
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def load_hazard_json(path, default):
    try:
        with open(path, encoding='utf-8') as data_file:
            data = json.load(data_file)
    except FileNotFoundError:
        return default
    if not isinstance(data, type(default)):
        raise ValueError('危険箇所データの形式が正しくありません。')
    return data


def save_hazard_json(path, data):
    temporary_path = None
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode='w', encoding='utf-8', dir=os.path.dirname(path), delete=False
        ) as data_file:
            temporary_path = data_file.name
            json.dump(data, data_file, ensure_ascii=False, indent=2)
        os.replace(temporary_path, path)
    except (OSError, TypeError, ValueError):
        if temporary_path and os.path.exists(temporary_path):
            try:
                os.remove(temporary_path)
            except OSError:
                pass
        raise


shelters = load_json(DATA_FILE, [])
instructions = load_json(INSTRUCTIONS_FILE, [])

def save_instructions(updated_instructions=None):
    """指示ボードのデータをファイルに保存する"""
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode='w', encoding='utf-8', dir=os.path.dirname(INSTRUCTIONS_FILE),
            delete=False
        ) as f:
            temporary_path = f.name
            data = instructions if updated_instructions is None else updated_instructions
            json.dump(
                [
                    {key: value for key, value in item.items() if key != 'urgency_class'}
                    for item in data
                ],
                f, ensure_ascii=False, indent=2
            )
        os.replace(temporary_path, INSTRUCTIONS_FILE)
    except (OSError, TypeError, ValueError):
        if temporary_path and os.path.exists(temporary_path):
            try:
                os.remove(temporary_path)
            except OSError:
                pass
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


def normalize_instruction(instruction):
    """古い指示データにも表示・編集用の既定値を補う"""
    normalized = dict(instruction)
    target = normalized.get('target', '')
    normalized.setdefault('urgency', '通常')
    normalized.setdefault('status', '発信中' if target == '住民' else '未対応')
    normalized.setdefault('shelter', '')
    normalized.setdefault('updated_at', normalized.get('created_at', ''))
    normalized.setdefault('created_at', '')
    normalized['urgency_class'] = {
        '緊急': 'urgent', '警戒': 'warning', '通常': 'normal'
    }.get(normalized.get('urgency'), 'normal')
    return normalized


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


def decode_polyline6(encoded):
    """Valhalla polyline6 を (latitude, longitude) の列に復号する。"""
    if not isinstance(encoded, str) or not encoded:
        raise ValueError('経路形状がありません。')
    coordinates = []
    index = latitude = longitude = 0
    while index < len(encoded):
        deltas = []
        for _ in range(2):
            result = shift = 0
            while True:
                if index >= len(encoded):
                    raise ValueError('経路形状が不完全です。')
                value = ord(encoded[index]) - 63
                index += 1
                if value < 0 or value > 63:
                    raise ValueError('経路形状が不正です。')
                result |= (value & 0x1f) << shift
                shift += 5
                if value < 0x20:
                    break
                if shift > 60:
                    raise ValueError('経路形状が不正です。')
            deltas.append(~(result >> 1) if result & 1 else result >> 1)
        latitude += deltas[0]
        longitude += deltas[1]
        point = (latitude / 1_000_000, longitude / 1_000_000)
        if not is_valid_coordinate(*point):
            raise ValueError('経路に有効でない座標があります。')
        coordinates.append(point)
    if len(coordinates) < 2:
        raise ValueError('経路形状を検証できません。')
    return coordinates


def danger_radius(place):
    radius = place.get('radius_m', 1000)
    if isinstance(radius, bool) or not isinstance(radius, (int, float)) or radius <= 0:
        return 1000
    return float(radius)


def distance_to_segment_meters(latitude, longitude, start, end):
    """局所平面近似で円中心から経路線分までの距離を返す。"""
    scale_y = 111_320
    scale_x = scale_y * math.cos(math.radians(latitude))
    if abs(scale_x) < 1:
        return min(
            calculate_distance_meters(latitude, longitude, *start),
            calculate_distance_meters(latitude, longitude, *end)
        )
    start_x = (start[1] - longitude) * scale_x
    start_y = (start[0] - latitude) * scale_y
    end_x = (end[1] - longitude) * scale_x
    end_y = (end[0] - latitude) * scale_y
    delta_x, delta_y = end_x - start_x, end_y - start_y
    length_squared = delta_x * delta_x + delta_y * delta_y
    if length_squared == 0:
        return math.hypot(start_x, start_y)
    fraction = max(0, min(1, -(start_x * delta_x + start_y * delta_y) / length_squared))
    return math.hypot(start_x + fraction * delta_x, start_y + fraction * delta_y)


def is_route_safe(coordinates, danger_places):
    """全頂点・全線分を検査し、危険円と余裕幅を避けた形状だけ許可する。"""
    if not isinstance(coordinates, list) or len(coordinates) < 2:
        return False
    try:
        for point in coordinates:
            if not isinstance(point, (list, tuple)) or len(point) != 2:
                return False
            if not is_valid_coordinate(point[0], point[1]):
                return False
        for place in danger_places:
            latitude, longitude = place.get('latitude'), place.get('longitude')
            if not is_valid_coordinate(latitude, longitude):
                return False
            safe_radius = danger_radius(place) + ROUTE_SAFETY_MARGIN_METERS
            for point in coordinates:
                if calculate_distance_meters(latitude, longitude, *point) <= safe_radius:
                    return False
            for start, end in zip(coordinates, coordinates[1:]):
                if distance_to_segment_meters(latitude, longitude, start, end) <= safe_radius:
                    return False
    except (TypeError, ValueError, OverflowError):
        return False
    return True


def danger_exclusion_polygon(place):
    """安全余裕を含む円を内包する Valhalla 用閉ポリゴンを返す。"""
    latitude, longitude = place['latitude'], place['longitude']
    radius = danger_radius(place) + ROUTE_SAFETY_MARGIN_METERS
    vertex_count = 20
    radius /= math.cos(math.pi / vertex_count)
    longitude_scale = 111_320 * max(0.01, math.cos(math.radians(latitude)))
    polygon = []
    for index in range(vertex_count):
        angle = 2 * math.pi * index / vertex_count
        polygon.append([
            longitude + radius * math.sin(angle) / longitude_scale,
            latitude + radius * math.cos(angle) / 111_320
        ])
    polygon.append(polygon[0])
    return polygon


def load_route_dangers():
    places = load_json(DANGER_PLACES_FILE, [])
    if not isinstance(places, list):
        return []
    valid_places = []
    for place in places:
        if not isinstance(place, dict) or not is_valid_coordinate(
            place.get('latitude'), place.get('longitude')
        ):
            continue
        normalized = dict(place)
        normalized['radius_m'] = danger_radius(place)
        valid_places.append(normalized)
    return valid_places


def get_relevant_route_dangers(origin, destination, danger_places):
    return [
        place for place in danger_places
        if distance_to_segment_meters(
            place['latitude'], place['longitude'], origin, destination
        ) <= ROUTE_EXCLUSION_CORRIDOR_METERS + danger_radius(place)
    ]


def get_valhalla_route(origin, destination, danger_places, validation_dangers=None):
    payload = {
        'locations': [
            {'lat': origin[0], 'lon': origin[1]},
            {'lat': destination[0], 'lon': destination[1]}
        ],
        'costing': 'pedestrian',
        'units': 'kilometers',
        'shape_format': 'polyline6',
        'exclude_polygons': [danger_exclusion_polygon(place) for place in danger_places]
    }
    route_request = urllib.request.Request(
        'https://valhalla1.openstreetmap.de/route',
        data=json.dumps(payload).encode('utf-8'),
        headers={'Content-Type': 'application/json', 'User-Agent': 'BousaiApp/1.0'},
        method='POST'
    )
    with urllib.request.urlopen(route_request, timeout=20) as response:
        route_data = json.loads(response.read())
    if not isinstance(route_data, dict):
        raise ValueError('経路サービスの応答を検証できません。')
    trip = route_data.get('trip')
    if not isinstance(trip, dict):
        raise ValueError('経路サービスの応答を検証できません。')
    legs = trip.get('legs')
    summary = trip.get('summary')
    if not isinstance(legs, list) or not legs or not isinstance(legs[0], dict):
        raise ValueError('安全な徒歩経路が見つかりません。')
    if not isinstance(summary, dict):
        raise ValueError('安全な徒歩経路が見つかりません。')
    coordinates = decode_polyline6(legs[0].get('shape'))
    if not is_route_safe(
        coordinates,
        danger_places if validation_dangers is None else validation_dangers
    ):
        raise ValueError('危険区域を避ける経路を確認できません。')
    distance_km, duration_s = summary.get('length'), summary.get('time')
    if (
        isinstance(distance_km, bool) or not isinstance(distance_km, (int, float))
        or not math.isfinite(distance_km) or distance_km <= 0
        or isinstance(duration_s, bool) or not isinstance(duration_s, (int, float))
        or not math.isfinite(duration_s) or duration_s < 0
    ):
        raise ValueError('徒歩経路の距離・所要時間を検証できません。')
    return {
        'distance_m': round(distance_km * 1000),
        'duration_s': round(duration_s),
        'geometry': {
            'type': 'LineString',
            'coordinates': [[longitude, latitude] for latitude, longitude in coordinates]
        }
    }


def get_active_resident_notices():
    return [
        normalize_instruction(instruction) for instruction in instructions
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
    # リダイレクト先を取得（デフォルトは管理者向け避難所登録画面）
    next_url = request.args.get('next') or request.form.get('next')

    # 安全でないURLの場合はデフォルトページにリダイレクト
    if not next_url or not is_safe_url(next_url):
        next_url = url_for('shelter_add')

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

@app.route('/shelter_register', methods=['GET', 'POST'], endpoint='shelter_register')
@app.route('/hazard_report', methods=['GET', 'POST'])
def hazard_report():
    form_data = request.form if request.method == 'POST' else {}

    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        address = request.form.get('address', '').strip()
        description = request.form.get('description', '').strip()
        hazard_type = request.form.get('type', '')
        try:
            latitude = float(request.form.get('latitude', ''))
            longitude = float(request.form.get('longitude', ''))
        except (TypeError, ValueError):
            latitude = longitude = float('nan')

        if not name or len(name) > 100:
            message = '名称を1〜100文字で入力してください。'
        elif len(address) > 300:
            message = '住所・名所は300文字以内で入力してください。'
        elif len(description) > 2000:
            message = '説明は2000文字以内で入力してください。'
        elif hazard_type not in HAZARD_TYPES:
            message = '危険の種類を選択してください。'
        elif not is_valid_coordinate(latitude, longitude):
            message = '地図で位置を指定し、正しい緯度・経度を入力してください。'
        else:
            message = None

        uploads = [upload for upload in request.files.getlist('attachments') if upload.filename]
        if message is None and len(uploads) > MAX_HAZARD_FILES:
            message = '添付ファイルは5件まで選択できます。'

        attachment_data = []
        if message is None:
            for upload in uploads:
                filename = upload.filename.replace('\\', '/').rsplit('/', 1)[-1]
                extension = os.path.splitext(filename)[1].lower()
                upload_type = HAZARD_UPLOAD_TYPES.get(extension)
                mime_type = (upload.mimetype or '').lower().split(';', 1)[0].strip()
                if not upload_type or mime_type not in upload_type[1]:
                    message = '対応する画像・動画形式のファイルを選択してください。'
                    break
                data = upload.read(MAX_HAZARD_FILE_SIZE + 1)
                if len(data) > MAX_HAZARD_FILE_SIZE:
                    message = '1ファイルは20MB以下にしてください。'
                    break
                attachment_data.append({
                    'data': data,
                    'display_name': filename[:255] or f'添付ファイル{len(attachment_data) + 1}',
                    'media_type': upload_type[0],
                    'mime_type': mime_type
                })

        if message:
            return render_template(
                'hazard_report.html', error=True, message=message,
                danger_types=HAZARD_TYPES, form_data=form_data
            )

        saved_paths = []
        try:
            attachments = []
            for upload in attachment_data:
                extension = os.path.splitext(upload['display_name'])[1].lower()
                stored_name = f'{uuid.uuid4().hex}{extension}'
                os.makedirs(HAZARD_UPLOAD_DIR, exist_ok=True)
                stored_path = os.path.join(HAZARD_UPLOAD_DIR, stored_name)
                with open(stored_path, 'xb') as stored_file:
                    stored_file.write(upload['data'])
                saved_paths.append(stored_path)
                attachments.append({
                    'display_name': upload['display_name'],
                    'url': url_for('static', filename=f'hazard_reports/{stored_name}'),
                    'media_type': upload['media_type'],
                    'mime_type': upload['mime_type'],
                    'size': len(upload['data'])
                })

            report = {
                'id': uuid.uuid4().hex,
                'name': name,
                'address': address,
                'type': hazard_type,
                'description': description,
                'latitude': latitude,
                'longitude': longitude,
                'attachments': attachments,
                'created_at': get_japan_time()
            }
            with HAZARD_STORAGE_LOCK:
                reports = load_hazard_json(HAZARD_REPORTS_FILE, [])
                save_hazard_json(HAZARD_REPORTS_FILE, reports + [report])
        except (OSError, ValueError, TypeError):
            for saved_path in saved_paths:
                if os.path.exists(saved_path):
                    try:
                        os.remove(saved_path)
                    except OSError:
                        pass
            return render_template(
                'hazard_report.html', error=True,
                message='危険箇所を保存できませんでした。もう一度お試しください。',
                danger_types=HAZARD_TYPES, form_data=form_data
            )

        return render_template(
            'hazard_report.html', success=True,
            message='危険箇所を投稿しました。',
            danger_types=HAZARD_TYPES, form_data={}
        )

    return render_template(
        'hazard_report.html', danger_types=HAZARD_TYPES, form_data=form_data
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
            normalize_instruction(instruction) for instruction in instructions
            if isinstance(instruction, dict)
        ]

        if action == 'create':
            content = request.form.get('content', '').strip()
            target = request.form.get('target', '')
            urgency = request.form.get('urgency', '')
            shelter_id = request.form.get('shelter_id', '')
            selected_shelter = next((
                shelter for shelter in shelters
                if str(shelter.get('id')) == shelter_id
            ), None) if shelter_id else None
            form_data = {
                'content': content, 'target': target, 'urgency': urgency,
                'shelter_id': shelter_id
            }
            if not content or len(content) > 1000:
                error = '指示・発信内容を1〜1000文字で入力してください。'
            elif target not in INSTRUCTION_TARGETS:
                error = '発信先を選択してください。'
            elif urgency not in INSTRUCTION_URGENCIES:
                error = '緊急度を選択してください。'
            elif shelter_id and selected_shelter is None:
                error = '登録済みの避難所を選択してください。'
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
                    'target': target,
                    'content': content,
                    'shelter': selected_shelter.get('name', '') if selected_shelter else '',
                    'urgency': urgency,
                    'status': '発信中' if target == '住民' else '未対応',
                    'created_at': now,
                    'updated_at': now
                })
                message = '指示・発信を登録しました。'
        elif action == 'edit_resident':
            instruction_id = request.form.get('instruction_id', '')
            content = request.form.get('content', '').strip()
            urgency = request.form.get('urgency', '')
            shelter_id = request.form.get('shelter_id', '')
            selected_shelter = next((
                shelter for shelter in shelters
                if str(shelter.get('id')) == shelter_id
            ), None) if shelter_id else None
            notice = next((
                instruction for instruction in updated_instructions
                if str(instruction.get('id')) == instruction_id
                and instruction.get('target') == '住民'
            ), None)
            if notice is None:
                error = '編集する住民向け発信が見つかりません。'
            elif not content or len(content) > 1000:
                error = '発信内容を1〜1000文字で入力してください。'
            elif urgency not in INSTRUCTION_URGENCIES:
                error = '有効な緊急度を選択してください。'
            elif shelter_id and selected_shelter is None:
                error = '登録済みの避難所を選択してください。'
            else:
                notice['content'] = content
                notice['urgency'] = urgency
                notice['shelter'] = selected_shelter.get('name', '') if selected_shelter else ''
                notice['updated_at'] = get_japan_time()
                message = '住民向け発信を更新しました。'
        elif action == 'update_status':
            instruction_id = request.form.get('instruction_id', '')
            status = request.form.get('status', '')
            notice = next((
                instruction for instruction in updated_instructions
                if str(instruction.get('id')) == instruction_id
            ), None)
            allowed_statuses = (
                RESIDENT_STATUSES if notice and notice.get('target') == '住民'
                else AGENCY_STATUSES
            )
            if notice is None:
                error = '状態を更新する指示が見つかりません。'
            elif status not in allowed_statuses:
                error = 'この発信先で選択できない対応状況です。'
            else:
                notice['status'] = status
                notice['updated_at'] = get_japan_time()
                message = '対応状況を更新しました。'
        else:
            error = '操作を確認できませんでした。'

        if message and save_instructions(updated_instructions):
            instructions[:] = [
                {key: value for key, value in instruction.items() if key != 'urgency_class'}
                for instruction in updated_instructions
            ]
        elif message:
            error = '指示・発信を保存できませんでした。もう一度お試しください。'
            message = None

    all_instructions = [
        normalize_instruction(instruction) for instruction in instructions
        if isinstance(instruction, dict)
    ]
    return render_template(
        'board.html',
        resident_instructions=[item for item in all_instructions if item.get('target') == '住民'],
        agency_instructions=[item for item in all_instructions if item.get('target') != '住民'],
        shelters=shelters,
        resident_statuses=RESIDENT_STATUSES,
        agency_statuses=AGENCY_STATUSES,
        targets=INSTRUCTION_TARGETS,
        urgencies=INSTRUCTION_URGENCIES,
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
            'address': shelter.get('address', ''),
            'latitude': shelter_latitude,
            'longitude': shelter_longitude,
            'distance_m': round(distance),
            '_sort_distance': distance
        })

    located_shelters.sort(key=lambda shelter: shelter['_sort_distance'])
    located_shelters = located_shelters[:5]
    for shelter in located_shelters:
        del shelter['_sort_distance']
    return jsonify({
        'shelters': located_shelters,
        'unlocated_count': unlocated_count
    })


@app.route('/api/shelters/reverse-geocode', methods=['POST'])
def api_shelter_reverse_geocode():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({'error': '有効な位置情報が必要です。'}), 400
    try:
        latitude = float(payload.get('latitude', ''))
        longitude = float(payload.get('longitude', ''))
    except (TypeError, ValueError):
        return jsonify({'error': '有効な位置情報が必要です。'}), 400
    if not is_valid_coordinate(latitude, longitude):
        return jsonify({'error': '有効な位置情報が必要です。'}), 400

    parameters = urlencode({'lat': latitude, 'lon': longitude})
    geocode_request = urllib.request.Request(
        f'https://mreversegeocoder.gsi.go.jp/reverse-geocoder/LonLatToAddress?{parameters}',
        headers={'User-Agent': 'BousaiApp/1.0'}
    )
    try:
        with urllib.request.urlopen(geocode_request, timeout=8) as response:
            geocode_data = json.loads(response.read())
    except (OSError, json.JSONDecodeError, TypeError):
        return jsonify({'error': '住所情報を取得できません。'}), 502
    results = geocode_data.get('results') if isinstance(geocode_data, dict) else None
    town = results.get('lv01Nm') if isinstance(results, dict) else None
    if not isinstance(town, str) or not town:
        return jsonify({'error': '住所情報を取得できません。'}), 404
    return jsonify({'town': town})


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

    if not isinstance(route_data, dict):
        return jsonify({'error': '徒歩ルートの応答を確認できません。'}), 502
    routes = route_data.get('routes', [])
    if not isinstance(routes, list):
        return jsonify({'error': '徒歩ルートの応答を確認できません。'}), 502
    if route_data.get('code') != 'Ok' or not routes:
        return jsonify({'error': 'この場所間の徒歩ルートが見つかりません。'}), 404

    route = routes[0]
    if not isinstance(route, dict):
        return jsonify({'error': '徒歩ルートの応答を確認できません。'}), 502
    legs = route.get('legs', [])
    if not isinstance(legs, list) or not legs or not isinstance(legs[0], dict):
        return jsonify({'error': '徒歩ルートの応答を確認できません。'}), 502
    steps = legs[0].get('steps', []) if legs else []
    if not isinstance(steps, list) or any(
        not isinstance(step, dict)
        or not isinstance(step.get('maneuver', {}), dict)
        or isinstance(step.get('distance', 0), bool)
        or not isinstance(step.get('distance', 0), (int, float))
        or not math.isfinite(step.get('distance', 0))
        or step.get('distance', 0) < 0
        for step in steps
    ):
        return jsonify({'error': '徒歩ルートの応答を確認できません。'}), 502
    geometry = route.get('geometry')
    coordinates = geometry.get('coordinates') if isinstance(geometry, dict) else None
    distance = route.get('distance')
    duration = route.get('duration')
    if (
        not isinstance(geometry, dict) or geometry.get('type') != 'LineString'
    ) or not isinstance(coordinates, list) or len(coordinates) < 2 or any(
        not isinstance(point, list) or len(point) < 2
        or not is_valid_coordinate(point[1], point[0])
        for point in coordinates
    ) or (
        isinstance(distance, bool) or not isinstance(distance, (int, float))
        or not math.isfinite(distance) or distance <= 0
        or isinstance(duration, bool) or not isinstance(duration, (int, float))
        or not math.isfinite(duration) or duration < 0
    ):
        return jsonify({'error': '徒歩ルートの応答を確認できません。'}), 502
    return jsonify({
        'distance_m': round(distance),
        'duration_s': round(duration),
        'geometry': geometry,
        'steps': [{
            'type': step.get('maneuver', {}).get('type', 'continue'),
            'modifier': step.get('maneuver', {}).get('modifier', ''),
            'name': step.get('name', ''),
            'distance_m': round(step.get('distance', 0))
        } for step in steps]
    })


@app.route('/evacuation_route')
def evacuation_route():
    return render_template('evacuation_route.html', shelters=shelters)


@app.route('/api/evacuation/geocode', methods=['POST'])
def api_evacuation_geocode():
    global GEOCODER_LAST_REQUEST_AT
    payload = request.get_json(silent=True)
    address = payload.get('address') if isinstance(payload, dict) else None
    if not isinstance(address, str) or not address.strip() or len(address) > 250:
        return jsonify({'error': '住所を入力してください。'}), 400
    address = address.strip()

    with GEOCODER_RATE_LIMIT_LOCK:
        now = time.monotonic()
        if now - GEOCODER_LAST_REQUEST_AT < 1:
            return jsonify({'error': '住所検索は1秒以上あけてお試しください。'}), 429
        GEOCODER_LAST_REQUEST_AT = now

    parameters = urlencode({
        'q': address,
        'format': 'jsonv2',
        'limit': 1,
        'countrycodes': 'jp',
        'viewbox': '136.55,35.35,137.15,34.95',
        'accept-language': 'ja'
    })
    geocode_request = urllib.request.Request(
        f'https://nominatim.openstreetmap.org/search?{parameters}',
        headers={'User-Agent': 'BousaiApp/1.0', 'Accept-Language': 'ja'}
    )
    try:
        with urllib.request.urlopen(geocode_request, timeout=15) as response:
            matches = json.loads(response.read())
    except (OSError, json.JSONDecodeError, TypeError):
        return jsonify({'error': '住所検索サービスを利用できません。時間をおいて再度お試しください。'}), 502
    if not isinstance(matches, list) or not matches:
        return jsonify({'error': '住所が見つかりません。入力内容をご確認ください。'}), 404
    try:
        latitude = float(matches[0]['lat'])
        longitude = float(matches[0]['lon'])
    except (KeyError, TypeError, ValueError):
        return jsonify({'error': '住所の位置を確認できません。'}), 502
    if not is_valid_coordinate(latitude, longitude):
        return jsonify({'error': '住所の位置を確認できません。'}), 502
    return jsonify({'latitude': latitude, 'longitude': longitude})


@app.route('/api/evacuation/danger_places', methods=['GET'])
def api_evacuation_danger_places():
    return jsonify(load_route_dangers())


@app.route('/api/evacuation/routes', methods=['POST'])
def api_evacuation_routes():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({'error': '有効な出発地点を指定してください。'}), 400
    try:
        latitude = float(payload.get('latitude', ''))
        longitude = float(payload.get('longitude', ''))
    except (TypeError, ValueError):
        return jsonify({'error': '有効な出発地点を指定してください。'}), 400
    if not is_valid_coordinate(latitude, longitude):
        return jsonify({'error': '有効な出発地点を指定してください。'}), 400

    danger_places = load_route_dangers()
    if any(
        calculate_distance_meters(
            latitude, longitude, place['latitude'], place['longitude']
        ) <= danger_radius(place) + ROUTE_SAFETY_MARGIN_METERS
        for place in danger_places
    ):
        return jsonify({
            'status': 'origin_in_danger',
            'error': '出発地点が危険範囲内です。別の場所を出発地に指定してください。',
            'results': []
        }), 409

    results = []
    provider_failures = 0
    for shelter in shelters:
        result = {'id': shelter.get('id'), 'name': shelter.get('name', '名称未設定')}
        destination_latitude = shelter.get('latitude')
        destination_longitude = shelter.get('longitude')
        if not is_valid_coordinate(destination_latitude, destination_longitude):
            result.update({'status': 'unlocated'})
        elif any(
            calculate_distance_meters(
                destination_latitude, destination_longitude,
                place['latitude'], place['longitude']
            ) <= danger_radius(place) + ROUTE_SAFETY_MARGIN_METERS
            for place in danger_places
        ):
            result.update({'status': 'danger_zone'})
        else:
            try:
                route_dangers = get_relevant_route_dangers(
                    (latitude, longitude),
                    (destination_latitude, destination_longitude),
                    danger_places
                )
                route = get_valhalla_route(
                    (latitude, longitude), (destination_latitude, destination_longitude),
                    route_dangers, validation_dangers=danger_places
                )
                result.update({'status': 'safe', **route})
            except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
                provider_failures += 1
                result.update({'status': 'route_unavailable'})
        results.append(result)

    results.sort(key=lambda result: (
        result.get('status') != 'safe', result.get('distance_m', float('inf'))
    ))
    return jsonify({
        'status': 'ok', 'results': results, 'provider_failures': provider_failures
    })


@app.errorhandler(413)
def hazard_upload_too_large(error):
    if request.path in ('/hazard_report', '/shelter_register'):
        return render_template(
            'hazard_report.html', error=True,
            message='投稿全体のサイズは50MB以下にしてください。',
            danger_types=HAZARD_TYPES, form_data={}
        ), 413
    return jsonify({'error': 'リクエストサイズが上限を超えています。'}), 413


@app.route('/api/hazard_reports/reverse-geocode', methods=['POST'])
def api_hazard_reverse_geocode():
    global HAZARD_GEOCODE_LAST_REQUEST_AT
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({'error': '地図上の位置を指定してください。'}), 400
    try:
        latitude = float(payload.get('latitude', ''))
        longitude = float(payload.get('longitude', ''))
    except (TypeError, ValueError):
        return jsonify({'error': '正しい緯度・経度を指定してください。'}), 400
    if not is_valid_coordinate(latitude, longitude):
        return jsonify({'error': '正しい緯度・経度を指定してください。'}), 400

    cache_key = (round(latitude, 5), round(longitude, 5))
    with HAZARD_GEOCODE_LOCK:
        if cache_key in HAZARD_GEOCODE_CACHE:
            return jsonify(HAZARD_GEOCODE_CACHE[cache_key])
        now = time.monotonic()
        if now - HAZARD_GEOCODE_LAST_REQUEST_AT < 1:
            return jsonify({'error': '場所検索中です。少し待ってから再度お試しください。'}), 429
        HAZARD_GEOCODE_LAST_REQUEST_AT = now

    parameters = urlencode({
        'lat': latitude,
        'lon': longitude,
        'format': 'jsonv2',
        'addressdetails': 1,
        'zoom': 18
    })
    geocode_request = urllib.request.Request(
        f'https://nominatim.openstreetmap.org/reverse?{parameters}',
        headers={'User-Agent': 'BousaiApp/1.0 (hazard-report)', 'Accept-Language': 'ja'}
    )
    try:
        with urllib.request.urlopen(geocode_request, timeout=8) as response:
            result = json.loads(response.read())
    except (OSError, json.JSONDecodeError, TypeError):
        return jsonify({'error': '場所検索を利用できません。手入力で投稿できます。'}), 502

    address_data = result.get('address', {}) if isinstance(result, dict) else {}
    facility = result.get('name', '') if isinstance(result, dict) else ''
    if not facility and isinstance(address_data, dict):
        facility = next((address_data.get(key, '') for key in (
            'amenity', 'shop', 'tourism', 'leisure', 'historic', 'building',
            'railway', 'public_transport'
        ) if address_data.get(key)), '')
    address = result.get('display_name', '') if isinstance(result, dict) else ''
    first_address = next((part.strip() for part in address.split(',') if part.strip()), '')
    suggestion = {'facility_name': facility, 'name': facility or first_address, 'address': address}
    with HAZARD_GEOCODE_LOCK:
        if len(HAZARD_GEOCODE_CACHE) >= 512:
            HAZARD_GEOCODE_CACHE.pop(next(iter(HAZARD_GEOCODE_CACHE)))
        HAZARD_GEOCODE_CACHE[cache_key] = suggestion
    return jsonify(suggestion)


@app.route('/api/hazard_spots', methods=['GET'])
def api_hazard_spots():
    try:
        reports = load_hazard_json(HAZARD_REPORTS_FILE, [])
        seen_records = load_hazard_json(HAZARD_SEEN_FILE, {})
    except (OSError, ValueError, TypeError):
        return jsonify({'error': '危険箇所データを読み込めません。'}), 500

    spots = []
    legacy_types = {
        'flood': '浸水・冠水', 'tsunami': '津波', 'landslide': '土砂崩れ',
        'fire': 'その他', 'road_block': '道路の損傷', 'other': 'その他'
    }
    legacy_places = load_json(DANGER_PLACES_FILE, [])
    if isinstance(legacy_places, list):
        for place in legacy_places:
            if not isinstance(place, dict) or not is_valid_coordinate(
                place.get('latitude'), place.get('longitude')
            ) or not isinstance(place.get('name'), str) or not place['name'].strip():
                continue
            attachments = []
            image_name = place.get('image')
            if isinstance(image_name, str) and image_name and os.path.basename(image_name) == image_name:
                image_url = url_for('static', filename=f'danger_places/{image_name}')
                image_path = os.path.join(app.static_folder, 'danger_places', image_name)
                attachments.append({
                    'display_name': image_name, 'url': image_url, 'media_type': 'image',
                    'mime_type': mimetypes.guess_type(image_name)[0] or 'image/jpeg',
                    'size': os.path.getsize(image_path) if os.path.isfile(image_path) else 0
                })
            spots.append({
                'id': str(place.get('id', 'legacy')),
                'name': place['name'], 'address': place.get('address', ''),
                'type': legacy_types.get(place.get('type'), 'その他'),
                'description': place.get('description', ''),
                'latitude': place['latitude'], 'longitude': place['longitude'],
                'attachments': attachments, 'created_at': place.get('created_at', ''),
                'seen_count': len(seen_records.get(str(place.get('id', 'legacy')), []))
            })

    for report in reports:
        if not isinstance(report, dict) or not is_valid_coordinate(
            report.get('latitude'), report.get('longitude')
        ):
            continue
        public_report = {key: report.get(key, '') for key in (
            'id', 'name', 'address', 'type', 'description', 'latitude',
            'longitude', 'attachments', 'created_at'
        )}
        public_report['seen_count'] = len(seen_records.get(str(report.get('id')), []))
        spots.append(public_report)
    return jsonify(spots)


@app.route('/api/hazard_spots/<spot_id>/seen', methods=['POST'])
def api_hazard_spot_seen(spot_id):
    payload = request.get_json(silent=True)
    browser_id = payload.get('browser_id') if isinstance(payload, dict) else None
    if not isinstance(browser_id, str) or not 16 <= len(browser_id) <= 128:
        return jsonify({'error': '確認情報を送信できませんでした。'}), 400
    voter_hash = hashlib.sha256(browser_id.encode('utf-8')).hexdigest()

    try:
        with HAZARD_STORAGE_LOCK:
            reports = load_hazard_json(HAZARD_REPORTS_FILE, [])
            legacy_places = load_json(DANGER_PLACES_FILE, [])
            exists = any(isinstance(report, dict) and str(report.get('id')) == spot_id for report in reports)
            if isinstance(legacy_places, list):
                exists = exists or any(
                    isinstance(place, dict) and str(place.get('id')) == spot_id
                    for place in legacy_places
                )
            if not exists:
                return jsonify({'error': '危険箇所が見つかりません。'}), 404
            seen_records = load_hazard_json(HAZARD_SEEN_FILE, {})
            voters = seen_records.get(spot_id, [])
            if not isinstance(voters, list):
                voters = []
            confirmed = voter_hash not in voters
            if confirmed:
                voters.append(voter_hash)
                seen_records[spot_id] = voters
                save_hazard_json(HAZARD_SEEN_FILE, seen_records)
    except (OSError, ValueError, TypeError):
        return jsonify({'error': '確認人数を保存できませんでした。'}), 500
    return jsonify({'confirmed': True, 'seen_count': len(voters)})


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
