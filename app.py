from flask import Flask, Response, jsonify, request, render_template_string
import json
import requests
from datetime import datetime, timedelta
import threading
import time
import os
import re
import logging
import secrets
from apscheduler.schedulers.background import BackgroundScheduler
from functools import wraps
import urllib.parse

app = Flask(__name__)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ============================================================
# USER DATABASE
# ============================================================

USERS_FILE = "users.json"
users_db = {}

def load_users():
    global users_db
    try:
        with open(USERS_FILE, "r") as f:
            users_db = json.load(f)
        logger.info(f"✅ Loaded {len(users_db)} users")
    except:
        users_db = {}
        logger.info("📝 Created new users database")

def save_users():
    with open(USERS_FILE, "w") as f:
        json.dump(users_db, f, indent=2)

load_users()

# ============================================================
# CUSTOM CHANNELS DATABASE
# ============================================================

CHANNELS_FILE = "channels.json"
channels_db = {
    "categories": {}
}

def load_channels():
    global channels_db
    try:
        with open(CHANNELS_FILE, "r") as f:
            channels_db = json.load(f)
        logger.info(f"✅ Loaded {len(channels_db.get('categories', {}))} categories")
    except:
        channels_db = {"categories": {}}
        logger.info("📝 Created new channels database")

def save_channels():
    with open(CHANNELS_FILE, "w") as f:
        json.dump(channels_db, f, indent=2)

load_channels()

# ============================================================
# AUTHENTICATION DECORATOR (No expiry check)
# ============================================================

def require_auth(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        uid = request.args.get('uid')
        password = request.args.get('pass')
        
        if not uid or not password:
            return jsonify({"error": "Missing uid or pass parameter"}), 401
        
        user = users_db.get(uid)
        if not user or user.get('password') != password:
            return jsonify({"error": "Invalid uid or pass"}), 401
        
        return f(*args, **kwargs)
    return decorated_function

# ============================================================
# USER MANAGEMENT FUNCTIONS
# ============================================================

def generate_uid():
    return ''.join(secrets.choice('abcdefghijklmnopqrstuvwxyz0123456789') for _ in range(10))

def generate_password():
    return ''.join(secrets.choice('abcdefghijklmnopqrstuvwxyz0123456789') for _ in range(8))

def create_user(uid=None, password=None):
    if not uid:
        uid = generate_uid()
    if not password:
        password = generate_password()
    
    users_db[uid] = {
        "uid": uid,
        "password": password,
        "created_at": int(datetime.now().timestamp()),
        "status": "active"
    }
    
    save_users()
    logger.info(f"✅ Created user: {uid} (permanent)")
    return uid, password

def get_user_info(uid):
    if uid not in users_db:
        return None
    
    user = users_db[uid].copy()
    user['status'] = user.get('status', 'active')
    return user

# ============================================================
# M3U PARSER FOR BULK IMPORT
# ============================================================

def parse_m3u_content(content):
    """Parse M3U content and extract channels"""
    channels = []
    lines = content.strip().split('\n')
    
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        
        if line.startswith('#EXTINF:'):
            # Parse EXTINF line
            channel_info = {
                'name': '',
                'logo': '',
                'group': 'Uncategorized',
                'tvg_id': '',
                'url': ''
            }
            
            # Extract attributes
            tvg_logo_match = re.search(r'tvg-logo="([^"]*)"', line)
            if tvg_logo_match:
                channel_info['logo'] = tvg_logo_match.group(1)
            
            group_match = re.search(r'group-title="([^"]*)"', line)
            if group_match:
                channel_info['group'] = group_match.group(1)
            
            tvg_id_match = re.search(r'tvg-id="([^"]*)"', line)
            if tvg_id_match:
                channel_info['tvg_id'] = tvg_id_match.group(1)
            
            # Extract channel name (after the last comma)
            if ',' in line:
                channel_info['name'] = line.split(',')[-1].strip()
            
            # Get URL from next non-comment line
            j = i + 1
            while j < len(lines):
                next_line = lines[j].strip()
                if next_line and not next_line.startswith('#'):
                    channel_info['url'] = next_line
                    break
                j += 1
            
            if channel_info['name'] and channel_info['url']:
                channels.append(channel_info)
            
            i = j + 1
        else:
            i += 1
    
    return channels

def fetch_and_import_sports_channels():
    """Fetch channels from FootyFeed sports.json and import them"""
    try:
        url = "https://footyfeedreal.pages.dev/sports.json"
        logger.info(f"🔄 Fetching channels from {url}...")
        
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        
        channels = parse_m3u_content(response.text)
        logger.info(f"📥 Parsed {len(channels)} channels from FootyFeed")
        
        # Group channels by category
        categories = {}
        for channel in channels:
            group = channel.get('group', 'Sports')
            if group not in categories:
                categories[group] = []
            
            # Check if channel already exists (avoid duplicates)
            existing = False
            if 'categories' in channels_db and group in channels_db['categories']:
                for existing_ch in channels_db['categories'][group]:
                    if existing_ch.get('url') == channel['url']:
                        existing = True
                        break
            
            if not existing:
                categories[group].append({
                    'name': channel['name'],
                    'url': channel['url'],
                    'logo': channel.get('logo', ''),
                    'keys': '',
                    'api': '',
                    'tokenApi': ''
                })
        
        # Add to channels_db
        if 'categories' not in channels_db:
            channels_db['categories'] = {}
        
        total_added = 0
        for group, channel_list in categories.items():
            if group not in channels_db['categories']:
                channels_db['categories'][group] = []
            
            channels_db['categories'][group].extend(channel_list)
            total_added += len(channel_list)
            logger.info(f"✅ Added {len(channel_list)} channels to '{group}'")
        
        save_channels()
        logger.info(f"✅ Total {total_added} new channels imported from FootyFeed")
        
        return {
            "status": "success",
            "total_parsed": len(channels),
            "total_added": total_added,
            "categories": {k: len(v) for k, v in categories.items()}
        }
        
    except Exception as e:
        logger.error(f"❌ Error importing FootyFeed channels: {e}")
        return {
            "status": "error",
            "message": str(e)
        }

# ============================================================
# GLOBAL VARIABLES
# ============================================================

current_playlist = None
last_update = None
update_lock = threading.Lock()
current_events_data = {
    "ivan": [],
    "fancode": [],
    "sonyliv": [],
    "custom": []
}

# ============================================================
# M3U GENERATOR CLASS
# ============================================================

class M3UGenerator:
    def __init__(self):
        self.playlist_content = None
        self.metadata = {
            "name": "FluX-oW Live event (Auto updated)",
            "author": "iVan_FluX",
            "contact": "https://t.me/iVan_flux",
            "channel": "https://t.me/api_hub_by_ivan"
        }

    def parse_date(self, date_str):
        if not date_str:
            return None
        try:
            cleaned = date_str.replace('/', '-').strip()
            if '+' in cleaned:
                cleaned = cleaned.split('+')[0].strip()
            elif '-' in cleaned and cleaned.count('-') > 2:
                parts = cleaned.split('-')
                if len(parts) > 3:
                    cleaned = '-'.join(parts[:3]) + ' ' + parts[3] if len(parts) > 3 else cleaned
            
            return datetime.strptime(cleaned, "%Y-%m-%d %H:%M:%S")
        except:
            try:
                return datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")
            except:
                try:
                    return datetime.strptime(date_str, "%Y/%m/%d %H:%M:%S")
                except:
                    return None

    def is_match_live(self, start_time, end_time=None):
        try:
            now = datetime.now()
            start = self.parse_date(start_time)
            if not start:
                return False
            if now < start:
                return False
            if end_time:
                end = self.parse_date(end_time)
                if end:
                    return now <= end
            end = start + timedelta(hours=3)
            return now <= end
        except:
            return False

    def get_category_from_title(self, title, cat=None):
        if cat:
            cat_lower = cat.lower()
            if 'cricket' in cat_lower:
                return 'Cricket'
            elif 'football' in cat_lower or 'soccer' in cat_lower:
                return 'Football'
            elif 'boxing' in cat_lower or 'wwe' in cat_lower:
                return 'Boxing/WWE'
            elif 'motorsport' in cat_lower or 'f1' in cat_lower or 'motogp' in cat_lower:
                return 'Motorsport'
            elif 'tennis' in cat_lower:
                return 'Tennis'
            elif 'baseball' in cat_lower:
                return 'Baseball'
            elif 'basketball' in cat_lower:
                return 'Basketball'
        
        if title:
            title_lower = title.lower()
            if 'cricket' in title_lower:
                return 'Cricket'
            elif 'football' in title_lower or 'soccer' in title_lower:
                return 'Football'
            elif 'boxing' in title_lower or 'wwe' in title_lower:
                return 'Boxing/WWE'
            elif 'motorsport' in title_lower or 'f1' in title_lower or 'motogp' in title_lower:
                return 'Motorsport'
            elif 'tennis' in title_lower:
                return 'Tennis'
            elif 'baseball' in title_lower:
                return 'Baseball'
            elif 'basketball' in title_lower:
                return 'Basketball'
        
        return 'Sports'

    def fetch_ivan_events(self):
        try:
            response = requests.get('https://events.ivan-flux.online/api/v1/user?username=valverdea', timeout=15)
            response.raise_for_status()
            data = response.json()
            
            events = data.get('events', [])
            live_events = []
            
            for event in events:
                event_info = event.get('eventInfo', {})
                start_time = event_info.get('startTime', '')
                end_time = event_info.get('endTime', '')
                status = event_info.get('Status', '')
                
                is_live = self.is_match_live(start_time, end_time)
                
                if status == 'Live' or is_live:
                    channels = event.get('channels_data', [])
                    filtered_channels = [
                        ch for ch in channels 
                        if ch.get('title') != 'Ivan-FluX' 
                        and ch.get('link') != 'https://fallback-video.ivan-fluxo.workers.dev/video/index.m3u8'
                        and ch.get('link')
                    ]
                    
                    if filtered_channels:
                        event['channels_data'] = filtered_channels
                        event['eventInfo']['Status'] = 'Live'
                        live_events.append(event)
            
            logger.info(f"✅ Found {len(live_events)} LIVE Ivan-Flux events")
            return live_events
        except Exception as e:
            logger.error(f"❌ Error fetching Ivan-Flux events: {e}")
            return []

    def fetch_fancode_events(self):
        try:
            response = requests.get('https://raw.githubusercontent.com/drmlive/fancode-live-events/refs/heads/main/fancode.json', timeout=15)
            response.raise_for_status()
            data = response.json()
            matches = data.get('matches', [])
            
            live_matches = []
            for match in matches:
                is_live = match.get('status', '').upper() == 'LIVE'
                has_stream = match.get('dai_url') or match.get('adfree_url')
                if is_live and has_stream:
                    live_matches.append(match)
            
            logger.info(f"✅ Found {len(live_matches)} LIVE Fancode events")
            return live_matches
        except Exception as e:
            logger.error(f"❌ Error fetching Fancode events: {e}")
            return []

    def fetch_sonyliv_events(self):
        try:
            sonyliv_url = os.environ.get('SONYLIV_JSON_URL', '')
            if sonyliv_url:
                try:
                    response = requests.get(sonyliv_url, timeout=15)
                    response.raise_for_status()
                    data = response.json()
                    matches = data.get('matches', [])
                    
                    live_matches = []
                    for match in matches:
                        if match.get('isLive', False):
                            has_stream = match.get('dai_url') or match.get('pub_url') or match.get('video_url')
                            if has_stream:
                                live_matches.append(match)
                    
                    logger.info(f"✅ Found {len(live_matches)} LIVE SonyLIV events")
                    return live_matches
                except:
                    pass
            return []
        except Exception as e:
            logger.error(f"❌ Error fetching SonyLIV events: {e}")
            return []

    def clean_link(self, link):
        if not link or link == "ok" or link == "":
            return None
        if link == "https://fallback-video.ivan-fluxo.workers.dev/video/index.m3u8":
            return None
        return link

    def process_ivan_event(self, event):
        channels = []
        event_info = event.get('eventInfo', {})
        event_name = event.get('title', 'Unknown Event')
        cat = event.get('cat', '')
        
        team_a = event_info.get('teamA', '')
        team_b = event_info.get('teamB', '')
        
        if team_a and team_b:
            title = f"🔴 LIVE {team_a} vs {team_b} - {event_name}"
        else:
            title = f"🔴 LIVE {event_name}"
        
        category = self.get_category_from_title(event_name, cat)
        
        for channel in event.get('channels_data', []):
            channel_title = channel.get('title', 'Stream')
            link = self.clean_link(channel.get('link', ''))
            
            if channel_title == 'Ivan-FluX':
                continue
            
            if link:
                channels.append({
                    'title': f"{title} - {channel_title}",
                    'link': link,
                    'logo': event.get('image', ''),
                    'group': category,
                    'keys': '',
                    'api': '',
                    'tokenApi': ''
                })
        
        return channels

    def process_fancode_event(self, event):
        channels = []
        
        dai_url = event.get('dai_url', '')
        adfree_url = event.get('adfree_url', '')
        
        title = event.get('title', 'Unknown Match')
        event_name = event.get('event_name', '')
        team_1 = event.get('team_1', '')
        team_2 = event.get('team_2', '')
        category = event.get('event_category', 'Sports')
        
        if team_1 and team_2:
            display_title = f"🔴 LIVE {team_1} vs {team_2}"
            if event_name:
                display_title = f"{display_title} ({event_name})"
        else:
            display_title = f"🔴 LIVE {title}"
        
        cat = self.get_category_from_title(category, category)
        
        if dai_url:
            clean_url = self.clean_link(dai_url)
            if clean_url:
                channels.append({
                    'title': f"{display_title} - Fancode Stream",
                    'link': clean_url,
                    'logo': event.get('src', ''),
                    'group': cat,
                    'keys': '',
                    'api': '',
                    'tokenApi': ''
                })
        
        if adfree_url and adfree_url != dai_url:
            clean_url = self.clean_link(adfree_url)
            if clean_url:
                channels.append({
                    'title': f"{display_title} - Adfree Stream",
                    'link': clean_url,
                    'logo': event.get('src', ''),
                    'group': cat,
                    'keys': '',
                    'api': '',
                    'tokenApi': ''
                })
        
        return channels

    def process_sonyliv_event(self, event):
        channels = []
        
        dai_url = event.get('dai_url', '')
        pub_url = event.get('pub_url', '')
        video_url = event.get('video_url', '')
        
        event_name = event.get('event_name', 'Unknown Event')
        match_name = event.get('match_name', '')
        broadcast_channel = event.get('broadcast_channel', '')
        category = event.get('event_category', 'Sports')
        
        if match_name:
            display_title = f"🔴 LIVE {match_name}"
        else:
            display_title = f"🔴 LIVE {event_name}"
        
        if broadcast_channel:
            display_title = f"{display_title} - {broadcast_channel}"
        
        cat = self.get_category_from_title(category, category)
        
        stream_urls = {
            'Main': dai_url,
            'Public': pub_url,
            'Video': video_url
        }
        
        for stream_type, url in stream_urls.items():
            if url:
                clean_url = self.clean_link(url)
                if clean_url:
                    channels.append({
                        'title': f"{display_title} ({stream_type})",
                        'link': clean_url,
                        'logo': event.get('src', ''),
                        'group': cat,
                        'keys': '',
                        'api': '',
                        'tokenApi': ''
                    })
        
        return channels

    def get_custom_channels(self):
        """Get custom channels from database"""
        channels = []
        for category, channel_list in channels_db.get('categories', {}).items():
            for channel in channel_list:
                channel_data = {
                    'title': channel.get('name', 'Custom Channel'),
                    'link': channel.get('url', ''),
                    'logo': channel.get('logo', ''),
                    'group': category,
                    'keys': channel.get('keys', ''),
                    'api': channel.get('api', ''),
                    'tokenApi': channel.get('tokenApi', '')
                }
                channels.append(channel_data)
        return channels

    def format_channel_url(self, link, keys='', api='', tokenApi=''):
        """Format the channel URL with proper MPD + DRM key support"""
        if not link:
            return link
        
        if '.mpd' in link:
            if keys:
                if '|' in link:
                    base, params = link.split('|', 1)
                    if 'drm-keys=' not in params:
                        return f"{base}|{params}|drm-keys={keys}"
                    else:
                        params = re.sub(r'drm-keys=[^|]*', f'drm-keys={keys}', params)
                        return f"{base}|{params}"
                else:
                    return f"{link}|drm-keys={keys}"
            
            if api and not keys:
                if '|' in link:
                    base, params = link.split('|', 1)
                    if 'api=' not in params:
                        return f"{base}|{params}|api={api}"
                    else:
                        params = re.sub(r'api=[^|]*', f'api={api}', params)
                        return f"{base}|{params}"
                else:
                    return f"{link}|api={api}"
            
            if api and tokenApi:
                if '|' in link:
                    base, params = link.split('|', 1)
                    new_params = params
                    if 'api=' not in new_params:
                        new_params = f"{new_params}|api={api}"
                    if 'tokenApi=' not in new_params:
                        new_params = f"{new_params}|tokenApi={tokenApi}"
                    return f"{base}|{new_params}"
                else:
                    return f"{link}|api={api}|tokenApi={tokenApi}"
        
        return link

    def generate_m3u(self, uid=None, password=None):
        global current_events_data
        
        logger.info("🔄 Starting playlist generation - LIVE EVENTS ONLY...")
        
        ivan_events = self.fetch_ivan_events()
        fancode_events = self.fetch_fancode_events()
        sonyliv_events = self.fetch_sonyliv_events()
        custom_channels = self.get_custom_channels()
        
        all_channels = []
        
        current_events_data['ivan'] = ivan_events
        for event in ivan_events:
            channels = self.process_ivan_event(event)
            all_channels.extend(channels)
        
        current_events_data['fancode'] = fancode_events
        for event in fancode_events:
            channels = self.process_fancode_event(event)
            all_channels.extend(channels)
        
        current_events_data['sonyliv'] = sonyliv_events
        for event in sonyliv_events:
            channels = self.process_sonyliv_event(event)
            all_channels.extend(channels)
        
        current_events_data['custom'] = custom_channels
        all_channels.extend(custom_channels)
        
        live_count = len(all_channels)
        logger.info(f"✅ Generated {live_count} LIVE stream channels")
        
        m3u_lines = []
        
        m3u_lines.append('#EXTM3U')
        m3u_lines.append(f'#PLAYLIST:{self.metadata["name"]}')
        m3u_lines.append(f'#AUTHOR:{self.metadata["author"]}')
        m3u_lines.append(f'#CONTACT (OWNER):{self.metadata["contact"]}')
        m3u_lines.append(f'#TELEGRAM CHANNEL:{self.metadata["channel"]}')
        m3u_lines.append(f'#Last update time:{datetime.now().strftime("%I:%M:%S %p %d-%m-%Y")}')
        m3u_lines.append(f'#Live Events:{live_count}')
        m3u_lines.append(f'#Source: Ivan-Flux: {len(ivan_events)} | Fancode: {len(fancode_events)} | SonyLIV: {len(sonyliv_events)} | Custom: {len(custom_channels)}')
        m3u_lines.append('')
        
        if uid:
            user = get_user_info(uid)
            if user:
                info_title = f"👤 USER: {uid} | Status: Active"
                m3u_lines.append(f'#EXTINF:0 group-title="🔐 User Info",{info_title}')
                m3u_lines.append('#User Info Channel - Permanent Access')
                m3u_lines.append('')
        
        for channel in all_channels:
            title = channel['title'].replace(',', ' ').replace('\n', ' ').strip()
            logo = channel.get('logo', '')
            group = channel.get('group', 'Sports')
            keys = channel.get('keys', '')
            api = channel.get('api', '')
            tokenApi = channel.get('tokenApi', '')
            
            link = self.format_channel_url(channel['link'], keys, api, tokenApi)
            
            extinf_parts = []
            if logo:
                extinf_parts.append(f'tvg-logo="{logo}"')
            if group:
                extinf_parts.append(f'group-title="{group}"')
            
            if keys:
                extinf_parts.append(f'drm-keys="{keys}"')
            
            if api:
                extinf_parts.append(f'api="{api}"')
            
            extinf_line = f'#EXTINF:0 {" ".join(extinf_parts)},{title}'
            m3u_lines.append(extinf_line)
            
            m3u_lines.append(link)
            m3u_lines.append('')
        
        self.playlist_content = '\n'.join(m3u_lines)
        return self.playlist_content

# Initialize generator
generator = M3UGenerator()

def update_playlist(uid=None, password=None):
    global current_playlist, last_update
    
    with update_lock:
        try:
            logger.info("🔄 Updating playlist...")
            start_time = time.time()
            current_playlist = generator.generate_m3u(uid, password)
            last_update = datetime.now()
            elapsed = time.time() - start_time
            logger.info(f"✅ Playlist updated successfully in {elapsed:.2f} seconds")
        except Exception as e:
            logger.error(f"❌ Error updating playlist: {e}")

# ============================================================
# USER PLAYLIST ENDPOINT
# ============================================================

@app.route('/user/<uid>')
def user_dashboard(uid):
    password = request.args.get('pass')
    
    if not password:
        return "Missing password parameter", 400
    
    user = users_db.get(uid)
    if not user or user.get('password') != password:
        return "Invalid credentials", 401
    
    base_url = request.url_root.rstrip('/')
    playlist_url = f"{base_url}/playlist.m3u?uid={uid}&pass={password}"
    created_date = datetime.fromtimestamp(user.get('created_at', 0)).strftime("%d %B %Y, %I:%M %p")
    
    return render_template_string('''
    <!DOCTYPE html>
    <html>
    <head>
        <title>🎬 Your Footy Feed IPTV Playlist</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            * { box-sizing: border-box; }
            body { 
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                max-width: 800px; 
                margin: 0 auto; 
                padding: 20px; 
                background: #1a1a2e; 
                color: #eee;
                min-height: 100vh;
            }
            .container { 
                background: #16213e; 
                padding: 30px; 
                border-radius: 12px; 
                box-shadow: 0 4px 20px rgba(0,0,0,0.5);
            }
            .header { 
                text-align: center;
                padding-bottom: 20px;
                border-bottom: 2px solid #2a2a4e;
                margin-bottom: 20px;
            }
            .header h1 { 
                color: #e94560; 
                margin: 0;
                font-size: 28px;
            }
            .header .subtitle { color: #888; margin-top: 5px; }
            .url-box { 
                background: #0d1b2a; 
                padding: 15px; 
                border-radius: 6px; 
                margin: 15px 0;
                border: 1px solid #1a3a5a;
                word-break: break-all;
            }
            .url-box code { 
                color: #8be9fd;
                font-size: 14px;
            }
            .warning-box {
                background: #2a1a1a;
                padding: 15px;
                border-radius: 6px;
                border-left: 4px solid #e94560;
                margin: 15px 0;
            }
            .warning-box strong { color: #e94560; }
            .validity-card {
                background: #0d1b2a;
                padding: 20px;
                border-radius: 8px;
                margin: 20px 0;
                border: 1px solid #1a3a5a;
            }
            .status-row {
                display: flex;
                justify-content: space-between;
                padding: 8px 0;
                border-bottom: 1px solid #1a1a2e;
            }
            .status-row:last-child { border-bottom: none; }
            .status-label { color: #888; }
            .status-value { font-weight: bold; }
            .button {
                display: inline-block;
                padding: 12px 30px;
                background: #e94560;
                color: white;
                text-decoration: none;
                border-radius: 6px;
                margin: 5px;
                border: none;
                cursor: pointer;
                font-size: 14px;
                font-weight: bold;
                transition: all 0.3s;
            }
            .button:hover { 
                background: #c73652;
                transform: scale(1.02);
            }
            .button-green { background: #0f3460; }
            .button-green:hover { background: #1a4a7a; }
            .button-blue { background: #1a6a8a; }
            .button-blue:hover { background: #2a7a9a; }
            .info-section {
                margin: 20px 0;
                padding: 15px;
                background: #0d1b2a;
                border-radius: 6px;
                border: 1px solid #1a3a5a;
            }
            .info-section h3 { 
                color: #8be9fd;
                margin-top: 0;
            }
            .footer {
                margin-top: 30px;
                color: #888;
                font-size: 14px;
                border-top: 1px solid #2a2a4e;
                padding-top: 20px;
                text-align: center;
            }
            .status-active {
                color: #50fa7b;
                font-weight: bold;
            }
            @media (max-width: 600px) {
                .container { padding: 15px; }
                .button { display: block; margin: 10px 0; }
                .status-row { flex-direction: column; }
            }
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>⚽ Your Footy Feed IPTV Playlist</h1>
                <div class="subtitle">🔗 M3U Playlist URL:</div>
            </div>
            
            <div class="url-box">
                <code>{{ playlist_url }}</code>
            </div>
            
            <div style="text-align: center; margin: 10px 0;">
                <button onclick="copyUrl()" class="button button-blue" style="padding: 8px 20px; font-size: 13px;">📋 Copy URL</button>
                <a href="{{ playlist_url }}" class="button button-green" style="padding: 8px 20px; font-size: 13px;">▶️ Open Playlist</a>
            </div>
            
            <div class="warning-box">
                <strong>⚠️ Warning:</strong> Do not share this link publicly. Sharing will result in immediate ban.
            </div>
            
            <div class="validity-card">
                <h3 style="margin-top: 0; color: #8be9fd;">📅 Account Details:</h3>
                
                <div class="status-row">
                    <span class="status-label">STATUS:</span>
                    <span class="status-value status-active">🟢 Active (Permanent)</span>
                </div>
                <div class="status-row">
                    <span class="status-label">USER ID:</span>
                    <span class="status-value" style="color: #8be9fd;">{{ uid }}</span>
                </div>
                <div class="status-row">
                    <span class="status-label">CREATED ON:</span>
                    <span class="status-value">{{ created_date }}</span>
                </div>
                <div class="status-row">
                    <span class="status-label">EXPIRY:</span>
                    <span class="status-value status-active">♾️ Never Expires</span>
                </div>
            </div>
            
            <div class="info-section">
                <h3>ℹ️ Important Information ℹ️</h3>
                <h4>🖥️ Supported Players:</h4>
                <p>Compatible with Tivimate, Sparkle TV, AuthoIPTV, Kodi, Network Stream Player and OTT Navigator.</p>
                <h4>🔰 Security Warning:</h4>
                <p>Do not share this link publicly. Sharing will result in immediate ban.</p>
                <h4>📱 Device Limit:</h4>
                <p>Maximum 2 devices allowed to stream simultaneously.</p>
                <h4>🎧 Technical Support:</h4>
                <p>Message @valverdeae on Telegram for support.</p>
            </div>
            
            <div class="footer">
                <p>Made with ❤️ | © 2024 Footy Feed IPTV</p>
            </div>
        </div>
        
        <script>
        function copyUrl() {
            const url = '{{ playlist_url }}';
            navigator.clipboard.writeText(url).then(() => {
                alert('✅ URL copied to clipboard!');
            }).catch(() => {
                const input = document.createElement('input');
                input.value = url;
                document.body.appendChild(input);
                input.select();
                document.execCommand('copy');
                document.body.removeChild(input);
                alert('✅ URL copied to clipboard!');
            });
        }
        </script>
    </body>
    </html>
    ''', 
    playlist_url=playlist_url, 
    uid=uid, 
    password=password,
    created_date=created_date)

# ============================================================
# AUTHENTICATED PLAYLIST ENDPOINT
# ============================================================

@app.route('/playlist.m3u')
@require_auth
def get_authenticated_playlist():
    global current_playlist
    
    uid = request.args.get('uid')
    password = request.args.get('pass')
    
    try:
        playlist_content = generator.generate_m3u(uid, password)
    except Exception as e:
        logger.error(f"Error generating playlist: {e}")
        playlist_content = current_playlist
    
    if playlist_content is None or playlist_content.count('#EXTINF:0') == 0:
        user = get_user_info(uid)
        if user:
            placeholder = f"""#EXTM3U
#PLAYLIST:No Live Events

#EXTINF:0 group-title="🔐 User Info",👤 USER: {uid} | Status: Active
#User Info Channel - Permanent Access

#EXTINF:-1,No Live Events
https://example.com/placeholder.m3u8
"""
            response = Response(placeholder, mimetype='application/vnd.apple.mpegurl')
            response.headers['Content-Disposition'] = 'inline; filename=live_playlist.m3u'
            return response
    
    user = get_user_info(uid)
    category_filter = request.args.get('category', '')
    
    content = playlist_content
    
    if category_filter:
        lines = content.split('\n')
        filtered_lines = []
        header_done = False
        i = 0
        while i < len(lines):
            line = lines[i]
            if line.startswith('#EXTM3U') or line.startswith('#PLAYLIST') or line.startswith('#AUTHOR') or \
               line.startswith('#CONTACT') or line.startswith('#TELEGRAM') or line.startswith('#Last update') or \
               line.startswith('#Live Events') or line.startswith('#Source'):
                filtered_lines.append(line)
                header_done = True
                i += 1
                continue
            
            if not header_done and 'User Info' in line:
                filtered_lines.append(line)
                if i + 1 < len(lines):
                    filtered_lines.append(lines[i + 1])
                    filtered_lines.append('')
                    i += 3
                    continue
            
            if line.startswith('#EXTINF:0'):
                match = re.search(r'group-title="([^"]+)"', line)
                if match:
                    group = match.group(1)
                    if group.lower() == category_filter.lower():
                        filtered_lines.append(line)
                        if i + 1 < len(lines):
                            filtered_lines.append(lines[i + 1])
                            filtered_lines.append('')
                i += 2
            else:
                if not line.startswith('#EXTINF:0') and not line.startswith('http') and not line.startswith('https'):
                    if not line.startswith('#Total Streams:'):
                        filtered_lines.append(line)
                i += 1
        
        if filtered_lines:
            content = '\n'.join(filtered_lines)
        else:
            return f"No live events in category: {category_filter}", 404
    
    header_lines = content.split('\n')
    user_info = [
        f'#USER: {uid}',
        f'#STATUS: Active (Permanent)',
        f'#CREATED: {datetime.fromtimestamp(user.get("created_at", 0)).strftime("%Y-%m-%d %H:%M:%S")}'
    ]
    
    if header_lines and header_lines[0].startswith('#EXTM3U'):
        header_lines = [header_lines[0]] + user_info + [''] + header_lines[1:]
    else:
        header_lines = ['#EXTM3U'] + user_info + [''] + header_lines
    
    content = '\n'.join(header_lines)
    
    response = Response(content, mimetype='application/vnd.apple.mpegurl')
    response.headers['Content-Disposition'] = 'inline; filename=live_playlist.m3u'
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response

# ============================================================
# ADMIN PANEL WITH MPD + DRM SUPPORT + BULK IMPORT
# ============================================================

@app.route('/adminpanel')
def admin_panel():
    admin_key = request.args.get('key', '')
    if admin_key != os.environ.get('ADMIN_KEY', 'admin123'):
        return render_template_string('''
        <!DOCTYPE html>
        <html>
        <head>
            <title>Admin Login</title>
            <meta name="viewport" content="width=device-width, initial-scale=1">
            <style>
                * { box-sizing: border-box; }
                body { 
                    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                    max-width: 400px; 
                    margin: 100px auto; 
                    padding: 20px; 
                    background: #1a1a2e; 
                    color: #eee;
                }
                .container { 
                    background: #16213e; 
                    padding: 30px; 
                    border-radius: 12px; 
                    box-shadow: 0 4px 20px rgba(0,0,0,0.5);
                }
                h1 { color: #e94560; text-align: center; }
                input, button {
                    width: 100%;
                    padding: 12px;
                    margin: 10px 0;
                    border-radius: 6px;
                    border: 1px solid #2a2a4e;
                    background: #0d1b2a;
                    color: #eee;
                    font-size: 16px;
                }
                button {
                    background: #e94560;
                    color: white;
                    font-weight: bold;
                    cursor: pointer;
                    border: none;
                }
                button:hover { background: #c73652; }
                .error { color: #e94560; text-align: center; margin: 10px 0; }
            </style>
        </head>
        <body>
            <div class="container">
                <h1>🔐 Admin Login</h1>
                <form method="get" action="/adminpanel">
                    <input type="password" name="key" placeholder="Enter Admin Key" required>
                    <button type="submit">Login</button>
                </form>
                <div class="error">Invalid admin key</div>
            </div>
        </body>
        </html>
        ''')
    
    categories = channels_db.get('categories', {})
    total_channels = sum(len(channels) for channels in categories.values())
    
    return render_template_string('''
    <!DOCTYPE html>
    <html>
    <head>
        <title>Admin Panel - Channel Management</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            * { box-sizing: border-box; }
            body { 
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                max-width: 1200px; 
                margin: 0 auto; 
                padding: 20px; 
                background: #1a1a2e; 
                color: #eee;
            }
            .container { 
                background: #16213e; 
                padding: 30px; 
                border-radius: 12px; 
                box-shadow: 0 4px 20px rgba(0,0,0,0.5);
            }
            .header { 
                display: flex;
                justify-content: space-between;
                align-items: center;
                border-bottom: 2px solid #2a2a4e;
                padding-bottom: 15px;
                margin-bottom: 20px;
                flex-wrap: wrap;
            }
            h1 { color: #e94560; margin: 0; }
            .stats {
                display: flex;
                gap: 20px;
                flex-wrap: wrap;
            }
            .stat {
                background: #0d1b2a;
                padding: 10px 20px;
                border-radius: 6px;
                text-align: center;
                border: 1px solid #1a3a5a;
            }
            .stat-number { font-size: 24px; font-weight: bold; color: #8be9fd; }
            .stat-label { font-size: 12px; color: #888; }
            .section {
                background: #0d1b2a;
                padding: 20px;
                border-radius: 8px;
                margin: 20px 0;
                border: 1px solid #1a3a5a;
            }
            .section h2 { color: #8be9fd; margin-top: 0; }
            .form-group {
                display: flex;
                flex-wrap: wrap;
                gap: 10px;
                margin: 10px 0;
            }
            .form-group input, .form-group select {
                padding: 10px;
                border-radius: 6px;
                border: 1px solid #2a2a4e;
                background: #1a1a2e;
                color: #eee;
                flex: 1;
                min-width: 150px;
            }
            .form-group button {
                padding: 10px 20px;
                background: #e94560;
                color: white;
                border: none;
                border-radius: 6px;
                cursor: pointer;
                font-weight: bold;
            }
            .form-group button:hover { background: #c73652; }
            .btn-success { background: #0f3460 !important; }
            .btn-success:hover { background: #1a4a7a !important; }
            .btn-danger { background: #e94560 !important; }
            .btn-danger:hover { background: #c73652 !important; }
            .btn-warning { background: #d4a017 !important; color: #1a1a2e !important; }
            .btn-warning:hover { background: #e6b422 !important; }
            .btn-import { background: #533483 !important; }
            .btn-import:hover { background: #6a44a0 !important; }
            table {
                width: 100%;
                border-collapse: collapse;
                margin-top: 10px;
            }
            th, td {
                padding: 12px;
                text-align: left;
                border-bottom: 1px solid #1a1a2e;
            }
            th { color: #8be9fd; }
            tr:hover { background: #1a1a2e; }
            .channel-list {
                max-height: 400px;
                overflow-y: auto;
            }
            .delete-btn {
                color: #e94560;
                cursor: pointer;
                background: none;
                border: none;
                font-size: 16px;
            }
            .delete-btn:hover { color: #ff6b7a; }
            .toast {
                padding: 15px;
                border-radius: 6px;
                margin: 10px 0;
                display: none;
            }
            .toast-success { background: #0f3460; color: #50fa7b; border: 1px solid #50fa7b; }
            .toast-error { background: #2a1a1a; color: #e94560; border: 1px solid #e94560; }
            .toast-info { background: #1a3a5a; color: #8be9fd; border: 1px solid #8be9fd; }
            .tabs {
                display: flex;
                gap: 10px;
                margin: 20px 0;
                flex-wrap: wrap;
            }
            .tab {
                padding: 10px 20px;
                background: #1a1a2e;
                border: 1px solid #2a2a4e;
                border-radius: 6px;
                cursor: pointer;
                color: #888;
            }
            .tab.active {
                background: #e94560;
                color: white;
                border-color: #e94560;
            }
            .tab-content { display: none; }
            .tab-content.active { display: block; }
            .keys-info {
                font-size: 11px;
                color: #f1fa8c;
                display: block;
                margin-top: 4px;
            }
            .drm-help {
                background: #1a1a2e;
                padding: 10px;
                border-radius: 4px;
                font-size: 12px;
                color: #888;
                margin-top: 5px;
                border-left: 3px solid #f1fa8c;
            }
            .drm-help code {
                color: #f1fa8c;
                background: #0d1b2a;
                padding: 2px 6px;
                border-radius: 3px;
            }
            .import-box {
                background: #1a1a2e;
                padding: 20px;
                border-radius: 8px;
                border: 2px dashed #533483;
                margin: 15px 0;
                text-align: center;
            }
            .import-box h3 {
                color: #8be9fd;
                margin-top: 0;
            }
            .import-box p {
                color: #888;
                margin: 10px 0;
            }
            .import-status {
                margin-top: 15px;
                padding: 10px;
                border-radius: 4px;
                display: none;
            }
            .category-badge {
                display: inline-block;
                background: #1a3a5a;
                padding: 2px 8px;
                border-radius: 4px;
                font-size: 11px;
                color: #8be9fd;
                margin-left: 8px;
            }
            @media (max-width: 600px) {
                .container { padding: 15px; }
                .form-group { flex-direction: column; }
                .form-group input, .form-group select { min-width: auto; }
                .stats { flex-wrap: wrap; }
                .header { flex-direction: column; align-items: stretch; }
            }
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>⚙️ Admin Panel</h1>
                <div class="stats">
                    <div class="stat">
                        <div class="stat-number">{{ total_categories }}</div>
                        <div class="stat-label">Categories</div>
                    </div>
                    <div class="stat">
                        <div class="stat-number">{{ total_channels }}</div>
                        <div class="stat-label">Total Channels</div>
                    </div>
                    <div class="stat">
                        <div class="stat-number">{{ total_users }}</div>
                        <div class="stat-label">Users</div>
                    </div>
                </div>
            </div>
            
            <div id="toast" class="toast"></div>
            
            <div class="tabs">
                <button class="tab active" onclick="showTab('categories')">📂 Categories</button>
                <button class="tab" onclick="showTab('add')">➕ Add Category</button>
                <button class="tab" onclick="showTab('import')">📥 Bulk Import</button>
                <button class="tab" onclick="showTab('users')">👥 Users</button>
                <button class="tab" onclick="showTab('create-user')">👤 Create User</button>
                <button class="tab" onclick="showTab('settings')">⚙️ Settings</button>
            </div>
            
            <!-- Categories Tab -->
            <div id="tab-categories" class="tab-content active">
                <h2>📂 Categories & Channels</h2>
                {% for category, channels in categories.items() %}
                <div class="section">
                    <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap;">
                        <h3 style="color: #50fa7b; margin: 0;">📁 {{ category }} <span class="category-badge">{{ channels|length }} channels</span></h3>
                        <div>
                            <button onclick="deleteCategory('{{ category }}')" class="btn-danger" style="padding: 5px 15px; font-size: 12px;">🗑️ Delete Category</button>
                        </div>
                    </div>
                    <div style="margin: 10px 0;">
                        <form onsubmit="addChannel(event, '{{ category }}')" style="display: flex; flex-wrap: wrap; gap: 10px;">
                            <input type="text" id="channel_name_{{ loop.index }}" placeholder="Channel Name" required style="flex: 1; min-width: 120px; padding: 8px; border-radius: 4px; border: 1px solid #2a2a4e; background: #1a1a2e; color: #eee;">
                            <input type="url" id="channel_url_{{ loop.index }}" placeholder="MPD URL" required style="flex: 2; min-width: 200px; padding: 8px; border-radius: 4px; border: 1px solid #2a2a4e; background: #1a1a2e; color: #8be9fd;">
                            <input type="text" id="channel_keys_{{ loop.index }}" placeholder="DRM Keys (key1=value1,key2=value2)" style="flex: 1.5; min-width: 150px; padding: 8px; border-radius: 4px; border: 1px solid #2a2a4e; background: #1a1a2e; color: #f1fa8c;">
                            <input type="text" id="channel_api_{{ loop.index }}" placeholder="API Key" style="flex: 1; min-width: 100px; padding: 8px; border-radius: 4px; border: 1px solid #2a2a4e; background: #1a1a2e; color: #50fa7b;">
                            <input type="text" id="channel_token_{{ loop.index }}" placeholder="Token API" style="flex: 1; min-width: 100px; padding: 8px; border-radius: 4px; border: 1px solid #2a2a4e; background: #1a1a2e; color: #50fa7b;">
                            <input type="text" id="channel_logo_{{ loop.index }}" placeholder="Logo URL" style="flex: 1; min-width: 100px; padding: 8px; border-radius: 4px; border: 1px solid #2a2a4e; background: #1a1a2e; color: #eee;">
                            <button type="submit" class="btn-success" style="padding: 8px 20px;">➕ Add Channel</button>
                        </form>
                        <div class="drm-help">
                            💡 <strong>For MPD streams:</strong> Put the MPD URL in "MPD URL" field. 
                            Put DRM keys in format: <code>key1=value1,key2=value2</code> or use API field for single key.
                        </div>
                    </div>
                    <div class="channel-list">
                        <table>
                            <thead>
                                <tr>
                                    <th>Name</th>
                                    <th>MPD URL</th>
                                    <th>DRM Keys</th>
                                    <th>API</th>
                                    <th style="width: 50px;">Action</th>
                                </tr>
                            </thead>
                            <tbody>
                                {% for channel in channels %}
                                <tr>
                                    <td>{{ channel.name }}</td>
                                    <td style="word-break: break-all; font-size: 12px; color: #8be9fd; max-width: 200px;">{{ channel.url }}</td>
                                    <td style="font-size: 12px; color: #f1fa8c; max-width: 150px; word-break: break-all;">
                                        {% if channel.keys %}
                                            {{ channel.keys }}
                                        {% else %}
                                            <span style="color: #888;">-</span>
                                        {% endif %}
                                    </td>
                                    <td style="font-size: 12px; color: #50fa7b; max-width: 150px; word-break: break-all;">
                                        {% if channel.api %}
                                            {{ channel.api }}
                                        {% else %}
                                            <span style="color: #888;">-</span>
                                        {% endif %}
                                    </td>
                                    <td>
                                        <button onclick="editFields('{{ category }}', {{ loop.index0 }})" class="delete-btn" style="color: #8be9fd; margin-right: 5px;" title="Edit">✏️</button>
                                        <button onclick="deleteChannel('{{ category }}', {{ loop.index0 }})" class="delete-btn">🗑️</button>
                                    </td>
                                </tr>
                                {% endfor %}
                            </tbody>
                        </table>
                    </div>
                </div>
                {% else %}
                <p style="color: #888; text-align: center; padding: 40px;">No categories found. Create one or import channels!</p>
                {% endfor %}
            </div>
            
            <!-- Add Category Tab -->
            <div id="tab-add" class="tab-content">
                <h2>➕ Add New Category</h2>
                <div class="section">
                    <form onsubmit="addCategory(event)">
                        <div class="form-group">
                            <input type="text" id="new_category_name" placeholder="Category Name (e.g., Sports, Movies, News)" required style="flex: 1;">
                            <button type="submit" class="btn-success">📁 Create Category</button>
                        </div>
                    </form>
                </div>
                
                <h2>🔧 Quick Actions</h2>
                <div class="section">
                    <div style="display: flex; flex-wrap: wrap; gap: 10px;">
                        <button onclick="updatePlaylist()" class="btn-success" style="padding: 12px 24px;">🔄 Update Playlist</button>
                        <button onclick="window.location.href='/update'" class="btn-success" style="padding: 12px 24px;">🔄 Force Refresh</button>
                    </div>
                </div>
            </div>
            
            <!-- Bulk Import Tab -->
            <div id="tab-import" class="tab-content">
                <h2>📥 Bulk Import Channels</h2>
                
                <div class="import-box">
                    <h3>🏈 Import Sports Channels from FootyFeed</h3>
                    <p>This will fetch all sports channels from <code style="color: #8be9fd;">https://footyfeedreal.pages.dev/sports.json</code></p>
                    <p>Channels will be organized by their group-title categories.</p>
                    <button onclick="importFootyFeed()" class="btn-import" style="padding: 15px 40px; font-size: 16px; margin: 15px 0;">
                        📥 Import Sports Channels
                    </button>
                    <div id="import-status" class="import-status"></div>
                </div>
                
                <div class="import-box" style="border-color: #0f3460;">
                    <h3>📋 Import from Custom M3U URL</h3>
                    <p>Enter any M3U playlist URL to import channels from.</p>
                    <div style="display: flex; flex-wrap: wrap; gap: 10px; margin-top: 15px;">
                        <input type="url" id="custom_m3u_url" placeholder="https://example.com/playlist.m3u" style="flex: 1; min-width: 250px; padding: 12px; border-radius: 6px; border: 1px solid #2a2a4e; background: #1a1a2e; color: #eee;">
                        <button onclick="importCustomM3U()" class="btn-success" style="padding: 12px 30px;">📥 Import</button>
                    </div>
                </div>
            </div>
            
            <!-- Users Tab -->
            <div id="tab-users" class="tab-content">
                <h2>👥 Users</h2>
                <div class="section">
                    <div style="overflow-x: auto;">
                        <table>
                            <thead>
                                <tr>
                                    <th>UID</th>
                                    <th>Password</th>
                                    <th>Created</th>
                                    <th>Status</th>
                                    <th>Actions</th>
                                </tr>
                            </thead>
                            <tbody>
                                {% for user in users %}
                                <tr>
                                    <td><code style="color: #8be9fd;">{{ user.uid }}</code></td>
                                    <td><code style="color: #50fa7b;">{{ user.password }}</code></td>
                                    <td style="font-size: 12px;">{{ user.created_at }}</td>
                                    <td><span style="color: #50fa7b;">🟢 Active</span></td>
                                    <td>
                                        <button onclick="deleteUser('{{ user.uid }}')" style="padding: 4px 12px; background: #e94560; border: none; border-radius: 4px; color: white; cursor: pointer; font-size: 12px;">🗑️</button>
                                    </td>
                                </tr>
                                {% endfor %}
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>
            
            <!-- Create User Tab -->
            <div id="tab-create-user" class="tab-content">
                <h2>👤 Create User</h2>
                <div class="section">
                    <form onsubmit="createUser(event)">
                        <div class="form-group">
                            <input type="text" id="create_uid" placeholder="UID (leave empty for auto-generate)" style="flex: 1;">
                            <input type="text" id="create_password" placeholder="Password (leave empty for auto-generate)" style="flex: 1;">
                            <button type="submit" class="btn-success">👤 Create User</button>
                        </div>
                    </form>
                </div>
                
                <h2>📋 Quick Create</h2>
                <div class="section">
                    <div style="display: flex; flex-wrap: wrap; gap: 10px;">
                        <button onclick="quickCreate()" class="btn-success" style="padding: 10px 20px;">👤 Create Permanent User</button>
                    </div>
                </div>
            </div>
            
            <!-- Settings Tab -->
            <div id="tab-settings" class="tab-content">
                <h2>⚙️ Settings</h2>
                <div class="section">
                    <p><strong>Playlist Status:</strong> 
                        {% if current_playlist %}
                        <span style="color: #50fa7b;">✅ Active</span>
                        {% else %}
                        <span style="color: #e94560;">❌ Not Generated</span>
                        {% endif %}
                    </p>
                    <p><strong>Last Update:</strong> {{ last_update_str or 'Never' }}</p>
                    <p><strong>Total Streams:</strong> {{ total_streams }}</p>
                    <p><strong>Total Users:</strong> {{ total_users }}</p>
                    <p><strong>Total Categories:</strong> {{ total_categories }}</p>
                    <p><strong>Total Channels:</strong> {{ total_channels }}</p>
                    <hr style="border-color: #2a2a4e;">
                    <div style="display: flex; flex-wrap: wrap; gap: 10px; margin-top: 10px;">
                        <button onclick="updatePlaylist()" class="btn-success" style="padding: 12px 24px;">🔄 Update Playlist</button>
                        <button onclick="window.location.href='/update'" class="btn-success" style="padding: 12px 24px;">🔄 Force Refresh</button>
                    </div>
                </div>
            </div>
        </div>
        
        <script>
        const adminKey = '{{ admin_key }}';
        const baseUrl = window.location.origin;
        
        function showTab(tabName) {
            document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
            document.querySelectorAll('.tab').forEach(el => el.classList.remove('active'));
            document.getElementById('tab-' + tabName).classList.add('active');
            document.querySelector(`.tab[onclick*="${tabName}"]`).classList.add('active');
        }
        
        function showToast(message, type = 'success') {
            const toast = document.getElementById('toast');
            toast.textContent = message;
            toast.className = 'toast toast-' + type;
            toast.style.display = 'block';
            setTimeout(() => {
                toast.style.display = 'none';
            }, 5000);
        }
        
        async function importFootyFeed() {
            const statusDiv = document.getElementById('import-status');
            statusDiv.style.display = 'block';
            statusDiv.className = 'import-status toast-info';
            statusDiv.textContent = '🔄 Importing channels from FootyFeed... This may take a moment.';
            
            try {
                const res = await fetch(`${baseUrl}/admin/import-footyfeed?key=${adminKey}`, {
                    method: 'POST'
                });
                const data = await res.json();
                
                if (data.status === 'success') {
                    statusDiv.className = 'import-status toast-success';
                    let msg = `✅ Imported ${data.total_added} channels!`;
                    if (data.categories) {
                        msg += '\\n\\nCategories:';
                        for (const [cat, count] of Object.entries(data.categories)) {
                            msg += `\\n• ${cat}: ${count}`;
                        }
                    }
                    statusDiv.textContent = msg;
                    showToast(`✅ Imported ${data.total_added} channels!`);
                    setTimeout(() => location.reload(), 3000);
                } else {
                    statusDiv.className = 'import-status toast-error';
                    statusDiv.textContent = '❌ Error: ' + (data.message || data.error);
                    showToast('❌ Import failed', 'error');
                }
            } catch (e) {
                statusDiv.className = 'import-status toast-error';
                statusDiv.textContent = '❌ Error: ' + e.message;
                showToast('❌ Import error', 'error');
            }
        }
        
        async function importCustomM3U() {
            const url = document.getElementById('custom_m3u_url').value.trim();
            if (!url) return showToast('Please enter a URL', 'error');
            
            const statusDiv = document.getElementById('import-status');
            statusDiv.style.display = 'block';
            statusDiv.className = 'import-status toast-info';
            statusDiv.textContent = '🔄 Importing from custom URL...';
            
            try {
                const res = await fetch(`${baseUrl}/admin/import-m3u?key=${adminKey}&url=${encodeURIComponent(url)}`, {
                    method: 'POST'
                });
                const data = await res.json();
                
                if (data.status === 'success') {
                    statusDiv.className = 'import-status toast-success';
                    statusDiv.textContent = `✅ Imported ${data.total_added} channels!`;
                    showToast(`✅ Imported ${data.total_added} channels!`);
                    setTimeout(() => location.reload(), 3000);
                } else {
                    statusDiv.className = 'import-status toast-error';
                    statusDiv.textContent = '❌ Error: ' + (data.message || data.error);
                    showToast('❌ Import failed', 'error');
                }
            } catch (e) {
                statusDiv.className = 'import-status toast-error';
                statusDiv.textContent = '❌ Error: ' + e.message;
                showToast('❌ Import error', 'error');
            }
        }
        
        async function addCategory(event) {
            event.preventDefault();
            const name = document.getElementById('new_category_name').value.trim();
            if (!name) return showToast('Please enter a category name', 'error');
            
            try {
                const res = await fetch(`${baseUrl}/admin/category?key=${adminKey}&name=${encodeURIComponent(name)}`, {
                    method: 'POST'
                });
                const data = await res.json();
                if (data.status === 'success') {
                    showToast('✅ Category created successfully!');
                    setTimeout(() => location.reload(), 1500);
                } else {
                    showToast('❌ ' + data.error, 'error');
                }
            } catch (e) {
                showToast('❌ Error creating category', 'error');
            }
        }
        
        async function addChannel(event, category) {
            event.preventDefault();
            const form = event.target;
            const inputs = form.querySelectorAll('input');
            const name = inputs[0].value.trim();
            const url = inputs[1].value.trim();
            const keys = inputs[2].value.trim();
            const api = inputs[3].value.trim();
            const tokenApi = inputs[4].value.trim();
            const logo = inputs[5].value.trim();
            
            if (!name || !url) return showToast('Please enter name and MPD URL', 'error');
            
            try {
                const res = await fetch(`${baseUrl}/admin/channel?key=${adminKey}&category=${encodeURIComponent(category)}&name=${encodeURIComponent(name)}&url=${encodeURIComponent(url)}&logo=${encodeURIComponent(logo)}&keys=${encodeURIComponent(keys)}&api=${encodeURIComponent(api)}&tokenApi=${encodeURIComponent(tokenApi)}`, {
                    method: 'POST'
                });
                const data = await res.json();
                if (data.status === 'success') {
                    showToast('✅ Channel added successfully!');
                    setTimeout(() => location.reload(), 1500);
                } else {
                    showToast('❌ ' + data.error, 'error');
                }
            } catch (e) {
                showToast('❌ Error adding channel', 'error');
            }
        }
        
        async function editFields(category, index) {
            const newKeys = prompt('Enter DRM keys (format: key1=value1,key2=value2):');
            if (newKeys === null) return;
            
            const newApi = prompt('Enter API key (or leave empty):');
            if (newApi === null) return;
            
            const newToken = prompt('Enter Token API (or leave empty):');
            if (newToken === null) return;
            
            try {
                const res = await fetch(`${baseUrl}/admin/channel/update?key=${adminKey}&category=${encodeURIComponent(category)}&index=${index}&keys=${encodeURIComponent(newKeys)}&api=${encodeURIComponent(newApi)}&tokenApi=${encodeURIComponent(newToken)}`, {
                    method: 'POST'
                });
                const data = await res.json();
                if (data.status === 'success') {
                    showToast('✅ Channel updated!');
                    setTimeout(() => location.reload(), 1500);
                } else {
                    showToast('❌ ' + data.error, 'error');
                }
            } catch (e) {
                showToast('❌ Error updating channel', 'error');
            }
        }
        
        async function deleteChannel(category, index) {
            if (!confirm('Delete this channel?')) return;
            try {
                const res = await fetch(`${baseUrl}/admin/channel/delete?key=${adminKey}&category=${encodeURIComponent(category)}&index=${index}`, {
                    method: 'DELETE'
                });
                const data = await res.json();
                if (data.status === 'success') {
                    showToast('✅ Channel deleted!');
                    setTimeout(() => location.reload(), 1500);
                } else {
                    showToast('❌ ' + data.error, 'error');
                }
            } catch (e) {
                showToast('❌ Error deleting channel', 'error');
            }
        }
        
        async function deleteCategory(category) {
            if (!confirm(`Delete category "${category}" and all its channels?`)) return;
            try {
                const res = await fetch(`${baseUrl}/admin/category/delete?key=${adminKey}&category=${encodeURIComponent(category)}`, {
                    method: 'DELETE'
                });
                const data = await res.json();
                if (data.status === 'success') {
                    showToast('✅ Category deleted!');
                    setTimeout(() => location.reload(), 1500);
                } else {
                    showToast('❌ ' + data.error, 'error');
                }
            } catch (e) {
                showToast('❌ Error deleting category', 'error');
            }
        }
        
        async function createUser(event) {
            event.preventDefault();
            const uid = document.getElementById('create_uid').value.trim();
            const password = document.getElementById('create_password').value.trim();
            
            let url = `${baseUrl}/admin/create-custom?key=${adminKey}`;
            if (uid) url += `&uid=${encodeURIComponent(uid)}`;
            if (password) url += `&pass=${encodeURIComponent(password)}`;
            
            try {
                const res = await fetch(url);
                const data = await res.json();
                if (data.status === 'success') {
                    showToast(`✅ User created: ${data.uid} (permanent)`);
                    setTimeout(() => location.reload(), 1500);
                } else {
                    showToast('❌ ' + data.error, 'error');
                }
            } catch (e) {
                showToast('❌ Error creating user', 'error');
            }
        }
        
        async function quickCreate() {
            try {
                const res = await fetch(`${baseUrl}/admin/create?key=${adminKey}`);
                const data = await res.json();
                if (data.status === 'success') {
                    showToast(`✅ User: ${data.uid} | Pass: ${data.password}`);
                    setTimeout(() => location.reload(), 1500);
                } else {
                    showToast('❌ ' + data.error, 'error');
                }
            } catch (e) {
                showToast('❌ Error creating user', 'error');
            }
        }
        
        async function deleteUser(uid) {
            if (!confirm(`Delete user "${uid}"?`)) return;
            try {
                const res = await fetch(`${baseUrl}/admin/user/delete?key=${adminKey}&uid=${encodeURIComponent(uid)}`, {
                    method: 'DELETE'
                });
                const data = await res.json();
                if (data.status === 'success') {
                    showToast('✅ User deleted!');
                    setTimeout(() => location.reload(), 1500);
                } else {
                    showToast('❌ ' + data.error, 'error');
                }
            } catch (e) {
                showToast('❌ Error deleting user', 'error');
            }
        }
        
        async function updatePlaylist() {
            try {
                showToast('🔄 Updating playlist...');
                const res = await fetch(`${baseUrl}/update`);
                const data = await res.json();
                if (data.status === 'success') {
                    showToast(`✅ Playlist updated! ${data.total_streams} streams`);
                } else {
                    showToast('❌ Update failed', 'error');
                }
            } catch (e) {
                showToast('❌ Error updating playlist', 'error');
            }
        }
        </script>
    </body>
    </html>
    ''', 
    categories=categories,
    total_categories=len(categories),
    total_channels=total_channels,
    total_users=len(users_db),
    users=[{
        'uid': uid,
        'password': user.get('password', ''),
        'created_at': datetime.fromtimestamp(user.get('created_at', 0)).strftime('%Y-%m-%d %H:%M')
    } for uid, user in users_db.items()],
    admin_key=admin_key,
    current_playlist=current_playlist,
    last_update_str=last_update.strftime('%Y-%m-%d %H:%M:%S') if last_update else None,
    total_streams=current_playlist.count('#EXTINF:0') if current_playlist else 0
)

# ============================================================
# BULK IMPORT ADMIN ENDPOINTS
# ============================================================

@app.route('/admin/import-footyfeed', methods=['POST'])
def admin_import_footyfeed():
    """Import sports channels from FootyFeed"""
    admin_key = request.args.get('key', '')
    if admin_key != os.environ.get('ADMIN_KEY', 'admin123'):
        return jsonify({"error": "Invalid admin key"}), 401
    
    result = fetch_and_import_sports_channels()
    
    if result['status'] == 'success':
        update_playlist()
    
    return jsonify(result)

@app.route('/admin/import-m3u', methods=['POST'])
def admin_import_m3u():
    """Import channels from a custom M3U URL"""
    admin_key = request.args.get('key', '')
    if admin_key != os.environ.get('ADMIN_KEY', 'admin123'):
        return jsonify({"error": "Invalid admin key"}), 401
    
    url = request.args.get('url', '')
    if not url:
        return jsonify({"error": "URL required"}), 400
    
    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        
        channels = parse_m3u_content(response.text)
        logger.info(f"📥 Parsed {len(channels)} channels from custom URL")
        
        # Group channels by category
        categories = {}
        for channel in channels:
            group = channel.get('group', 'Imported')
            if group not in categories:
                categories[group] = []
            
            # Check for duplicates
            existing = False
            if 'categories' in channels_db and group in channels_db['categories']:
                for existing_ch in channels_db['categories'][group]:
                    if existing_ch.get('url') == channel['url']:
                        existing = True
                        break
            
            if not existing:
                categories[group].append({
                    'name': channel['name'],
                    'url': channel['url'],
                    'logo': channel.get('logo', ''),
                    'keys': '',
                    'api': '',
                    'tokenApi': ''
                })
        
        # Add to channels_db
        if 'categories' not in channels_db:
            channels_db['categories'] = {}
        
        total_added = 0
        for group, channel_list in categories.items():
            if group not in channels_db['categories']:
                channels_db['categories'][group] = []
            
            channels_db['categories'][group].extend(channel_list)
            total_added += len(channel_list)
        
        save_channels()
        update_playlist()
        
        return jsonify({
            "status": "success",
            "total_parsed": len(channels),
            "total_added": total_added,
            "categories": {k: len(v) for k, v in categories.items() if v}
        })
        
    except Exception as e:
        logger.error(f"❌ Error importing custom M3U: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

# ============================================================
# ADMIN API ENDPOINTS
# ============================================================

@app.route('/admin/category', methods=['POST'])
def admin_add_category():
    admin_key = request.args.get('key', '')
    if admin_key != os.environ.get('ADMIN_KEY', 'admin123'):
        return jsonify({"error": "Invalid admin key"}), 401
    
    name = request.args.get('name', '').strip()
    if not name:
        return jsonify({"error": "Category name required"}), 400
    
    if 'categories' not in channels_db:
        channels_db['categories'] = {}
    
    if name in channels_db['categories']:
        return jsonify({"error": "Category already exists"}), 400
    
    channels_db['categories'][name] = []
    save_channels()
    logger.info(f"✅ Added category: {name}")
    return jsonify({"status": "success", "message": f"Category '{name}' created"})

@app.route('/admin/channel', methods=['POST'])
def admin_add_channel():
    admin_key = request.args.get('key', '')
    if admin_key != os.environ.get('ADMIN_KEY', 'admin123'):
        return jsonify({"error": "Invalid admin key"}), 401
    
    category = request.args.get('category', '').strip()
    name = request.args.get('name', '').strip()
    url = request.args.get('url', '').strip()
    logo = request.args.get('logo', '').strip()
    keys = request.args.get('keys', '').strip()
    api = request.args.get('api', '').strip()
    tokenApi = request.args.get('tokenApi', '').strip()
    
    if not category or not name or not url:
        return jsonify({"error": "Category, name, and URL required"}), 400
    
    if 'categories' not in channels_db:
        channels_db['categories'] = {}
    
    if category not in channels_db['categories']:
        return jsonify({"error": "Category not found"}), 404
    
    channels_db['categories'][category].append({
        "name": name,
        "url": url,
        "logo": logo,
        "keys": keys,
        "api": api,
        "tokenApi": tokenApi
    })
    save_channels()
    update_playlist()
    logger.info(f"✅ Added channel '{name}' to '{category}'")
    return jsonify({"status": "success", "message": f"Channel '{name}' added to '{category}'"})

@app.route('/admin/channel/update', methods=['POST'])
def admin_update_channel():
    admin_key = request.args.get('key', '')
    if admin_key != os.environ.get('ADMIN_KEY', 'admin123'):
        return jsonify({"error": "Invalid admin key"}), 401
    
    category = request.args.get('category', '').strip()
    index = request.args.get('index', type=int)
    keys = request.args.get('keys', '').strip()
    api = request.args.get('api', '').strip()
    tokenApi = request.args.get('tokenApi', '').strip()
    
    if index is None:
        return jsonify({"error": "Index required"}), 400
    
    if 'categories' not in channels_db or category not in channels_db['categories']:
        return jsonify({"error": "Category not found"}), 404
    
    if index >= len(channels_db['categories'][category]):
        return jsonify({"error": "Channel not found"}), 404
    
    if keys:
        channels_db['categories'][category][index]['keys'] = keys
    if api:
        channels_db['categories'][category][index]['api'] = api
    if tokenApi:
        channels_db['categories'][category][index]['tokenApi'] = tokenApi
    
    save_channels()
    update_playlist()
    logger.info(f"✅ Updated channel '{channels_db['categories'][category][index]['name']}' in '{category}'")
    return jsonify({"status": "success", "message": "Channel updated"})

@app.route('/admin/channel/delete', methods=['DELETE'])
def admin_delete_channel():
    admin_key = request.args.get('key', '')
    if admin_key != os.environ.get('ADMIN_KEY', 'admin123'):
        return jsonify({"error": "Invalid admin key"}), 401
    
    category = request.args.get('category', '').strip()
    index = request.args.get('index', type=int)
    
    if index is None:
        return jsonify({"error": "Index required"}), 400
    
    if 'categories' not in channels_db or category not in channels_db['categories']:
        return jsonify({"error": "Category not found"}), 404
    
    if index >= len(channels_db['categories'][category]):
        return jsonify({"error": "Channel not found"}), 404
    
    removed = channels_db['categories'][category].pop(index)
    save_channels()
    update_playlist()
    logger.info(f"✅ Deleted channel '{removed.get('name')}' from '{category}'")
    return jsonify({"status": "success", "message": "Channel deleted"})

@app.route('/admin/category/delete', methods=['DELETE'])
def admin_delete_category():
    admin_key = request.args.get('key', '')
    if admin_key != os.environ.get('ADMIN_KEY', 'admin123'):
        return jsonify({"error": "Invalid admin key"}), 401
    
    category = request.args.get('category', '').strip()
    
    if 'categories' not in channels_db or category not in channels_db['categories']:
        return jsonify({"error": "Category not found"}), 404
    
    del channels_db['categories'][category]
    save_channels()
    update_playlist()
    logger.info(f"✅ Deleted category: {category}")
    return jsonify({"status": "success", "message": f"Category '{category}' deleted"})

@app.route('/admin/create', methods=['GET'])
def admin_create_user_short():
    admin_key = request.args.get('key', '')
    if admin_key != os.environ.get('ADMIN_KEY', 'admin123'):
        return jsonify({"error": "Invalid admin key"}), 401
    
    uid, password = create_user()
    
    return jsonify({
        "status": "success",
        "uid": uid,
        "password": password,
        "playlist_url": f"{request.url_root}playlist.m3u?uid={uid}&pass={password}",
        "user_dashboard": f"{request.url_root}user/{uid}?pass={password}"
    })

@app.route('/admin/create-custom', methods=['GET'])
def admin_create_custom_user():
    admin_key = request.args.get('key', '')
    if admin_key != os.environ.get('ADMIN_KEY', 'admin123'):
        return jsonify({"error": "Invalid admin key"}), 401
    
    uid = request.args.get('uid', '')
    password = request.args.get('pass', '')
    
    if not uid or not password:
        return jsonify({"error": "Missing uid or pass"}), 400
    
    if uid in users_db:
        return jsonify({"error": "User already exists"}), 400
    
    users_db[uid] = {
        "uid": uid,
        "password": password,
        "created_at": int(datetime.now().timestamp()),
        "status": "active"
    }
    
    save_users()
    logger.info(f"✅ Created custom user: {uid} (permanent)")
    
    return jsonify({
        "status": "success",
        "uid": uid,
        "password": password,
        "playlist_url": f"{request.url_root}playlist.m3u?uid={uid}&pass={password}",
        "user_dashboard": f"{request.url_root}user/{uid}?pass={password}"
    })

@app.route('/admin/user/delete', methods=['DELETE'])
def admin_delete_user():
    admin_key = request.args.get('key', '')
    if admin_key != os.environ.get('ADMIN_KEY', 'admin123'):
        return jsonify({"error": "Invalid admin key"}), 401
    
    uid = request.args.get('uid', '')
    if not uid:
        return jsonify({"error": "UID required"}), 400
    
    if uid not in users_db:
        return jsonify({"error": "User not found"}), 404
    
    del users_db[uid]
    save_users()
    logger.info(f"✅ Deleted user: {uid}")
    return jsonify({"status": "success", "message": f"User '{uid}' deleted"})

# ============================================================
# ORIGINAL ENDPOINTS
# ============================================================

@app.route('/')
def index():
    base_url = request.url_root.rstrip('/')
    
    total_streams = 0
    categories = {}
    
    if current_playlist:
        total_streams = current_playlist.count('#EXTINF:0')
        
        lines = current_playlist.split('\n')
        for line in lines:
            if line.startswith('#EXTINF:0') and 'group-title="' in line:
                match = re.search(r'group-title="([^"]+)"', line)
                if match:
                    category = match.group(1)
                    categories[category] = categories.get(category, 0) + 1
    
    next_update = (last_update + timedelta(hours=1)).strftime("%I:%M:%S %p") if last_update else "Not set"
    last_update_str = last_update.strftime("%I:%M:%S %p %d-%m-%Y") if last_update else "Never"
    
    category_buttons = ''.join([
        f'<button class="category-btn" data-category="{cat}">{cat} ({count})</button>'
        for cat, count in sorted(categories.items())
    ])
    
    html = f'''
    <!DOCTYPE html>
    <html>
    <head>
        <title>🔴 LIVE M3U Playlist</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            * {{ box-sizing: border-box; }}
            body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 1000px; margin: 0 auto; padding: 20px; background: #1a1a2e; color: #eee; }}
            .container {{ background: #16213e; padding: 30px; border-radius: 12px; box-shadow: 0 4px 20px rgba(0,0,0,0.5); }}
            h1 {{ color: #e94560; margin-top: 0; display: flex; align-items: center; gap: 10px; }}
            .live-badge {{ background: #e94560; color: white; padding: 4px 12px; border-radius: 20px; font-size: 14px; animation: pulse 1.5s infinite; }}
            @keyframes pulse {{ 0% {{ opacity: 1; }} 50% {{ opacity: 0.4; }} 100% {{ opacity: 1; }} }}
            .status {{ padding: 15px; background: #1a1a2e; border-radius: 8px; margin: 20px 0; border: 1px solid #2a2a4e; }}
            .info {{ background: #1a1a2e; padding: 15px; border-radius: 8px; margin: 10px 0; border: 1px solid #2a2a4e; }}
            .button {{ display: inline-block; padding: 12px 24px; background: #e94560; color: white; text-decoration: none; border-radius: 6px; margin: 5px; border: none; cursor: pointer; font-size: 14px; }}
            .button:hover {{ background: #c73652; }}
            .button-green {{ background: #0f3460; }}
            .button-green:hover {{ background: #1a4a7a; }}
            .button-orange {{ background: #e94560; }}
            .button-orange:hover {{ background: #c73652; }}
            .button-purple {{ background: #533483; }}
            .button-purple:hover {{ background: #6a44a0; }}
            .footer {{ margin-top: 30px; color: #888; font-size: 14px; border-top: 1px solid #2a2a4e; padding-top: 20px; }}
            .badge {{ display: inline-block; padding: 3px 10px; border-radius: 4px; font-size: 12px; font-weight: bold; }}
            .badge-green {{ background: #0f3460; color: #eee; }}
            .badge-red {{ background: #e94560; color: white; }}
            code {{ background: #0d1b2a; padding: 10px; display: block; border-radius: 6px; word-break: break-all; font-size: 13px; border: 1px solid #1a3a5a; color: #8be9fd; }}
            .url-box {{ background: #0d1b2a; padding: 15px; border-radius: 6px; margin: 10px 0; border: 1px solid #1a3a5a; }}
            .stats-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 10px; margin: 15px 0; }}
            .stat-card {{ background: #0d1b2a; padding: 15px; border-radius: 6px; text-align: center; border: 1px solid #1a3a5a; }}
            .stat-number {{ font-size: 28px; font-weight: bold; color: #e94560; }}
            .stat-label {{ font-size: 12px; color: #888; margin-top: 5px; }}
            .category-buttons {{ display: flex; flex-wrap: wrap; gap: 10px; margin: 15px 0; }}
            .category-btn {{ padding: 8px 16px; background: #1a1a2e; border: 1px solid #2a2a4e; border-radius: 20px; cursor: pointer; font-size: 13px; color: #eee; }}
            .category-btn:hover {{ background: #2a2a4e; }}
            .category-btn.active {{ background: #e94560; border-color: #e94560; color: white; }}
            @media (max-width: 600px) {{ .container {{ padding: 15px; }} .button {{ display: block; margin: 10px 0; }} }}
            .live-dot {{ display: inline-block; width: 12px; height: 12px; background: #e94560; border-radius: 50%; animation: pulse 1s infinite; margin-right: 8px; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1><span class="live-dot"></span>LIVE M3U Playlist <span class="live-badge">🔴 LIVE</span></h1>
            
            <div class="status">
                <strong>Status:</strong> <span class="badge badge-red">● Live</span>
                <br><br>
                <strong>Last Update:</strong> {last_update_str}
                <br>
                <strong>Next Update:</strong> {next_update}
                <br>
                <strong>Live Streams:</strong> <span style="color: #e94560; font-weight: bold;">{total_streams}</span>
            </div>
            
            <div class="stats-grid">
                <div class="stat-card">
                    <div class="stat-number">{total_streams}</div>
                    <div class="stat-label">🔴 Live Streams</div>
                </div>
                <div class="stat-card">
                    <div class="stat-number" style="color: #8be9fd;">{len(categories)}</div>
                    <div class="stat-label">📂 Categories</div>
                </div>
            </div>
            
            <div class="category-buttons">
                <button class="category-btn active" data-category="all">All ({total_streams})</button>
                {category_buttons}
            </div>
            
            <div style="margin: 20px 0;">
                <a href="/playlist.m3u" class="button">📥 Get LIVE Playlist</a>
                <a href="/update" class="button button-orange">🔄 Refresh</a>
                <a href="/stats" class="button button-purple">📊 Stats</a>
            </div>
            
            <div class="info">
                <h3>📋 Playlist URL</h3>
                <div class="url-box">
                    <code>{base_url}/playlist.m3u</code>
                </div>
                <button onclick="copyUrl()" class="button" style="padding: 8px 16px; font-size: 12px; background: #0f3460;">📋 Copy URL</button>
            </div>
            
            <div style="margin-top: 20px;">
                <h3>📱 How to Use</h3>
                <ol>
                    <li>Copy the URL above</li>
                    <li>Open your IPTV player (VLC, TiviMate, IPTV Extreme, etc.)</li>
                    <li>Add a new playlist and paste the URL</li>
                    <li>The playlist updates <strong>every hour</strong> with only LIVE events</li>
                </ol>
            </div>
            
            <div class="footer">
                <p>🤖 Powered by Ivan-Flux, Fancode &amp; SonyLIV | Updates every hour</p>
            </div>
        </div>
        
        <script>
        function copyUrl() {{
            const url = '{base_url}/playlist.m3u';
            navigator.clipboard.writeText(url).then(() => {{ alert('✅ URL copied!'); }}).catch(() => {{
                const input = document.createElement('input');
                input.value = url;
                document.body.appendChild(input);
                input.select();
                document.execCommand('copy');
                document.body.removeChild(input);
                alert('✅ URL copied!');
            }});
        }}
        
        document.querySelectorAll('.category-btn').forEach(btn => {{
            btn.addEventListener('click', function() {{
                document.querySelectorAll('.category-btn').forEach(b => b.classList.remove('active'));
                this.classList.add('active');
                const category = this.dataset.category;
                if (category === 'all') {{
                    window.location.href = '/playlist.m3u';
                }} else {{
                    window.location.href = '/playlist.m3u?category=' + encodeURIComponent(category);
                }}
            }});
        }});
        </script>
    </body>
    </html>
    '''
    
    return html

@app.route('/update')
def force_update():
    update_playlist()
    return jsonify({
        "status": "success",
        "message": "Playlist updated",
        "last_update": last_update.strftime("%I:%M:%S %p %d-%m-%Y") if last_update else "Never",
        "total_streams": current_playlist.count('#EXTINF:0') if current_playlist else 0
    })

@app.route('/stats')
def get_stats():
    stats = {
        "status": "ready" if current_playlist else "not_generated",
        "last_update": last_update.strftime("%I:%M:%S %p %d-%m-%Y") if last_update else "Never",
        "total_streams": 0,
        "categories": {},
        "sources": {
            "ivan_flux": len(current_events_data.get('ivan', [])),
            "fancode": len(current_events_data.get('fancode', [])),
            "sonyliv": len(current_events_data.get('sonyliv', [])),
            "custom": len(current_events_data.get('custom', []))
        }
    }
    
    if current_playlist:
        stats["total_streams"] = current_playlist.count('#EXTINF:0')
        
        lines = current_playlist.split('\n')
        for line in lines:
            if line.startswith('#EXTINF:0') and 'group-title="' in line:
                match = re.search(r'group-title="([^"]+)"', line)
                if match:
                    category = match.group(1)
                    stats["categories"][category] = stats["categories"].get(category, 0) + 1
    
    return jsonify(stats)

@app.route('/raw')
def get_raw_data():
    return jsonify({
        "live_events": {
            "ivan_flux": current_events_data.get('ivan', []),
            "fancode": current_events_data.get('fancode', []),
            "sonyliv": current_events_data.get('sonyliv', []),
            "custom": current_events_data.get('custom', [])
        }
    })

# ============================================================
# TEST ENDPOINT
# ============================================================

@app.route('/test')
def test():
    """Test endpoint to check if server is running"""
    return jsonify({
        "status": "ok",
        "message": "Server is running",
        "timestamp": datetime.now().isoformat(),
        "users": len(users_db)
    })

# ============================================================
# MAIN
# ============================================================

# Initialize scheduler
scheduler = BackgroundScheduler()
scheduler.add_job(update_playlist, 'interval', hours=1, id='playlist_update')
scheduler.start()

# Initial update
update_playlist()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port, debug=False)
