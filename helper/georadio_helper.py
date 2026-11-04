"""Geo Radio helper for BeamNG.drive + BeamNG.Earth.

The game cannot reach the internet or decode audio streams, so this program does it:
  * the geoRadio Lua extension connects to 127.0.0.1:PORT and sends the car's real lat/lon
    and where the camera is (inside the car or not, how far);
  * we reverse-geocode it (OpenStreetMap Nominatim) and look up stations (radio-browser.info):
    stations with coordinates within the radius, plus stations of the same city / region;
  * the chosen station plays through the Windows default audio output (and follows it when
    that device is changed in the system settings), with static mixed in as its signal fades
    out of range, muffled outside the car in the realistic sound mode.

Wire format: one JSON object per line (UTF-8), both directions.
"""

import collections
import ctypes
import json
import math
import os
import random
import re
import socket
import sys
import threading
import time
import traceback
import urllib.parse
import urllib.request
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
if sys.stdout is None:          # pythonw (the autostart): no console, log to a file
    sys.stdout = sys.stderr = open(os.path.join(HERE, 'helper.log'), 'a', encoding='utf-8', buffering=1)

import av
import numpy as np
import sounddevice as sd
from scipy.signal import butter, lfilter

VERSION = '0.3.2'
HOST, PORT = '127.0.0.1', 47620
UA = 'BeamNG-GeoRadio/' + VERSION
CONFIG_PATH = os.path.join(HERE, 'config.json')
SONGS_TXT = os.path.join(HERE, 'saved_songs.txt')
STARTUP_VBS = os.path.join(os.environ.get('APPDATA', ''), 'Microsoft', 'Windows', 'Start Menu', 'Programs',
                           'Startup', 'GeoRadio.vbs')

RATE = 48000
DEFAULT_CONFIG = {
    'volume': 0.6, 'radius_km': 80, 'lang': 'en',
    'sound_mode': 'realistic',     # 'realistic': loud in the cabin, muffled outside | 'practical': always the same
    'mute_unfocused': True,        # silent while the game window is not in front (minimized, alt-tabbed)
    'mute_paused': True,
    'follow_master': True,         # scaled by the game's master volume
    'need_ignition': True,         # realistic mode: the radio needs the ignition on
    'auto_resume': True,           # play the last station again when the game connects
    'car_screen': True,            # (read by the game) station and song on the car's navigation screen
    'autostart': False,            # start hidden with Windows
    'favorites': {},
    'last_station': None,
    'was_playing': True,           # False after the player pressed pause: then nothing is resumed
    'songs': [],                   # bookmarked songs, newest first
}
SETTABLE = {'sound_mode', 'mute_unfocused', 'mute_paused', 'follow_master', 'need_ignition', 'auto_resume',
            'car_screen', 'autostart'}
MAX_SONGS = 500
TUNE_S = 0.7                   # the static between two stations when tuning with << >>

RB_MIRRORS = ['de1', 'de2', 'fi1', 'nl1', 'at1']

LOOKUP_MOVE_KM = 3.0           # look stations up again after moving this far
LOOKUP_MAX_AGE_S = 600
GEOCODE_MIN_INTERVAL_S = 1.2   # Nominatim usage policy: at most 1 request per second
WEAK_SIGNAL = 0.6              # the playing station's signal under this: suggest local ones
LOST_STOP_S = 20               # a station silent (signal 0) this long is stopped
MAX_LISTED = 40
GAME_GONE_STOP_S = 3           # the game disconnected this long: stop playing
STREAM_IDLE_CLOSE_S = 5        # the audio device is released this long after the radio stops
MUFFLE_HZ = 700                # the cut-off of the car body, heard from outside


def log(*a):
    print(time.strftime('%H:%M:%S'), *a, flush=True)


def clamp(x, lo=0.0, hi=1.0):
    return lo if x < lo else hi if x > hi else x


def haversine_km(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 6371.0 * 2 * math.asin(min(1.0, math.sqrt(a)))


def norm(s):
    s = (s or '').lower()
    s = re.sub(r'\(.*?\)', ' ', s)
    s = re.sub(r"\b(oblast|region|province|state|county|krai|kray|republic of|city of|область|край|республика)\b", ' ', s)
    return re.sub(r'[\s\-_.,]+', ' ', s).strip()


FREQ_RE = re.compile(r'(?<![\d.,])(\d{2,3})[.,](\d{1,2})(?!\d)')
FREQ_INT_RE = re.compile(r'(?<![\d.,])(8[89]|9\d|10[0-7])(?![\d.,])\s*(?:fm|фм|mhz|мгц)', re.I)


def parse_freq(name, tags=''):
    """The FM frequency a station's name or tags mention ('DFM Ufa 91.5 FM' -> 91.5), or None."""
    for text in (name or '', tags or ''):
        for m in FREQ_RE.finditer(text):
            f = float(m.group(1) + '.' + m.group(2))
            if 87.5 <= f <= 108.0:
                return round(f, 1)
    m = FREQ_INT_RE.search(name or '')
    return float(m.group(1)) if m else None


def load_config():
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    try:
        with open(CONFIG_PATH, encoding='utf-8') as f:
            cfg.update(json.load(f))
    except FileNotFoundError:
        pass
    except Exception as e:
        log('config.json unreadable, using defaults:', e)
    return cfg


def save_config(cfg):
    try:
        tmp = CONFIG_PATH + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        os.replace(tmp, CONFIG_PATH)
    except Exception as e:
        log('cannot save config:', e)


def apply_autostart(on):
    try:
        if on:
            pyw = os.path.join(os.path.dirname(sys.executable), 'pythonw.exe')
            if not os.path.exists(pyw):
                pyw = sys.executable
            with open(STARTUP_VBS, 'w', encoding='utf-16') as f:
                f.write('CreateObject("WScript.Shell").Run """%s"" ""%s""", 0, False\r\n' % (pyw, os.path.abspath(__file__)))
            log('autostart on:', STARTUP_VBS)
        elif os.path.exists(STARTUP_VBS):
            os.remove(STARTUP_VBS)
            log('autostart off')
    except Exception as e:
        log('autostart:', e)


def foreground_exe():
    """The file name of the program whose window is in front, lower case ('' when unknown)."""
    try:
        user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), ctypes.byref(pid))
        h = kernel32.OpenProcess(0x1000, False, pid.value)     # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return ''
        buf = ctypes.create_unicode_buffer(1024)
        size = ctypes.c_ulong(1024)
        ok = kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size))
        kernel32.CloseHandle(h)
        return os.path.basename(buf.value).lower() if ok else ''
    except Exception:
        return ''


def http_json(url, timeout=12):
    req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept': 'application/json'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode('utf-8', 'replace'))


# ---------------------------------------------------------------- radio-browser / nominatim

class RadioBrowser:
    def __init__(self):
        self.mirrors = list(RB_MIRRORS)
        random.shuffle(self.mirrors)

    def query(self, path, **params):
        params.setdefault('hidebroken', 'true')
        qs = urllib.parse.urlencode(params)
        last = None
        for m in list(self.mirrors):
            url = 'https://%s.api.radio-browser.info%s?%s' % (m, path, qs)
            try:
                return http_json(url)
            except Exception as e:
                last = e
                self.mirrors.remove(m)
                self.mirrors.append(m)
        raise RuntimeError('radio-browser unreachable: %s' % last)

    def click(self, uuid):
        try:
            self.query('/json/url/' + uuid)
        except Exception:
            pass


class Geocoder:
    def __init__(self):
        self.cache = {}
        self.last_call = 0.0

    def reverse(self, lat, lon):
        key = (round(lat, 2), round(lon, 2))
        if key in self.cache:
            return self.cache[key]
        out = {}
        for lang in ('en', None):   # English names and the local ones: radio-browser has either
            wait = GEOCODE_MIN_INTERVAL_S - (time.time() - self.last_call)
            if wait > 0:
                time.sleep(wait)
            self.last_call = time.time()
            url = 'https://nominatim.openstreetmap.org/reverse?format=jsonv2&zoom=10&lat=%.5f&lon=%.5f' % (lat, lon)
            if lang:
                url += '&accept-language=' + lang
            addr = http_json(url).get('address', {})
            city = addr.get('city') or addr.get('town') or addr.get('village') or addr.get('municipality') or ''
            state = addr.get('state') or addr.get('region') or addr.get('province') or addr.get('county') or ''
            if lang:
                out.update(city=city, state=state, country=addr.get('country', ''),
                           cc=(addr.get('country_code') or '').upper())
            else:
                out.update(city_local=city, state_local=state, country_local=addr.get('country', ''))
        self.cache[key] = out
        return out


# ---------------------------------------------------------------- stations and their signal

RAW_KEYS = ('stationuuid', 'name', 'url', 'url_resolved', 'codec', 'bitrate', 'tags', 'state', 'countrycode',
            'geo_lat', 'geo_long', 'clickcount', 'votes')


class Station:
    def __init__(self, raw, kind):
        self.raw = {k: raw.get(k) for k in RAW_KEYS}
        self.id = raw['stationuuid']
        self.name = (raw.get('name') or '?').strip()
        self.url = raw.get('url_resolved') or raw.get('url')
        self.codec = raw.get('codec') or ''
        self.bitrate = raw.get('bitrate') or 0
        self.tags = [t for t in (raw.get('tags') or '').split(',') if t][:6]
        self.state = raw.get('state') or ''
        self.cc = (raw.get('countrycode') or '').upper()
        self.votes = (raw.get('clickcount') or 0) + (raw.get('votes') or 0)
        lat, lon = raw.get('geo_lat'), raw.get('geo_long')
        self.lat = float(lat) if lat not in (None, '') else None
        self.lon = float(lon) if lon not in (None, '') else None
        # 'local' (has coordinates) | 'regional' | 'national' | 'online' (a favourite played anywhere, no fading)
        self.kind = 'online' if kind == 'online' else 'local' if self.lat is not None else kind
        self.freq = parse_freq(self.name, raw.get('tags'))
        self.anchor = None       # regional / national: the last point where we were inside its area
        self.signal = 1.0 if kind == 'online' else 0.0
        self.dist = None


class Finder:
    """Keeps the stations around the car and their signal strength."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.rb = RadioBrowser()
        self.geo = Geocoder()
        self.stations = {}
        self.region = {}
        self.region_keys = set()
        self.region_at = None   # where self.region was looked up: it only describes the area around that point
        self.status = 'idle'
        self.lookup_at = None
        self.lookup_time = 0
        self.busy = False
        self.lock = threading.Lock()
        self.on_region_change = None
        self.pos = None        # the latest position (a lookup finishes seconds after it started)
        self.keep = None       # the playing station's id: never dropped

    def radius(self):
        return float(self.cfg.get('radius_km', 80))

    def maybe_lookup(self, lat, lon, force=False):
        if self.busy:
            return
        moved = self.lookup_at is None or haversine_km(lat, lon, *self.lookup_at) > LOOKUP_MOVE_KM
        if force or moved or time.time() - self.lookup_time > LOOKUP_MAX_AGE_S:
            self.busy = True
            threading.Thread(target=self._lookup, args=(lat, lon), daemon=True).start()

    def _lookup(self, lat, lon):
        try:
            self.status = 'busy'
            region = self.geo.reverse(lat, lon)
            r = self.radius()
            found = []
            found += [(s, 'local') for s in self.rb.query(
                '/json/stations/search', geo_lat='%.5f' % lat, geo_long='%.5f' % lon,
                geo_distance=int(r * 1.5 * 1000), order='clickcount', reverse='true', limit=200)]
            cc = region.get('cc')
            names = {region.get(k) for k in ('state', 'state_local', 'city', 'city_local')} - {'', None}
            for name in names:
                if not cc:
                    break
                found += [(s, 'regional') for s in self.rb.query(
                    '/json/stations/search', countrycode=cc, state=name, order='clickcount',
                    reverse='true', limit=80)]
            if cc and len(found) < 8:
                found += [(s, 'national') for s in self.rb.query(
                    '/json/stations/search', countrycode=cc, order='clickcount', reverse='true', limit=30)]
            with self.lock:
                if self.pos and haversine_km(lat, lon, *self.pos) > LOOKUP_MOVE_KM * 2:
                    log('lookup at %.3f, %.3f is stale (moved since), looking again' % (lat, lon))
                    return             # lookup_at stays old: the next position starts a new lookup
                changed = norm(region.get('state')) != norm(self.region.get('state'))
                self.region, self.region_at = region, (lat, lon)
                self.region_keys = {norm(n) for n in names if norm(n)}
                for raw, kind in found:
                    if not (raw.get('url_resolved') or raw.get('url')):
                        continue
                    st = self.stations.get(raw['stationuuid'])
                    if st is None:
                        st = self.stations[raw['stationuuid']] = Station(raw, kind)
                    elif st.kind == 'national' and kind == 'regional':
                        st.kind = 'regional'
                self.update_signal(*(self.pos or (lat, lon)))
            self.lookup_at, self.lookup_time = (lat, lon), time.time()
            self.status = 'ok'
            log('lookup: %s / %s / %s -> %d stations nearby' % (
                region.get('city'), region.get('state'), region.get('country'),
                sum(1 for s in self.stations.values() if s.signal > 0)))
            if changed and self.on_region_change:
                self.on_region_change(region)
        except Exception as e:
            self.status = 'error: %s' % e
            log('lookup failed:', e)
        finally:
            self.busy = False

    def _in_region(self, st):
        if st.kind == 'national':
            return st.cc == self.region.get('cc')
        if st.cc and st.cc != self.region.get('cc'):
            return False
        s = norm(st.state)
        return bool(s) and any(s == k or s in k or k in s for k in self.region_keys)

    def update_signal(self, lat, lon):
        """Call with self.lock held."""
        r = self.radius()
        keep = self.keep
        # until the region around here is looked up, self.region is the old place's: no station is "in" it
        fresh = self.region_at is not None and haversine_km(lat, lon, *self.region_at) <= LOOKUP_MOVE_KM * 2
        for sid, st in list(self.stations.items()):
            if st.kind == 'local':
                st.dist = haversine_km(lat, lon, st.lat, st.lon)
                st.signal = clamp((r * 1.4 - st.dist) / (r * 0.4))
            else:
                if fresh and self._in_region(st):
                    st.anchor = (lat, lon)
                if st.anchor is None:
                    st.signal = 0.0
                else:
                    gone = haversine_km(lat, lon, *st.anchor)
                    st.signal = clamp(1 - gone / (r * 0.4))
                st.dist = None
            if st.signal <= 0 and sid != keep:
                del self.stations[sid]

    def teleport(self):
        """The car jumped somewhere else: nothing of the old place is on the air here."""
        with self.lock:
            self.stations = {k: v for k, v in self.stations.items() if k == self.keep}
            self.region, self.region_keys, self.region_at = {}, set(), None
            self.lookup_at = None
            if not self.busy:
                self.status = 'busy'

    def listed(self):
        with self.lock:
            sts = [s for s in self.stations.values() if s.signal > 0.02]
        order = {'local': 0, 'regional': 1, 'national': 2}
        sts.sort(key=lambda s: (-round(s.signal, 1), order.get(s.kind, 3),
                                s.dist if s.dist is not None else 1e9, -s.votes))
        seen, out = set(), []
        for s in sts:
            key = norm(s.name)
            if key not in seen:
                seen.add(key)
                out.append(s)
        return out[:MAX_LISTED]

    def get(self, sid):
        with self.lock:
            return self.stations.get(sid)


# ---------------------------------------------------------------- audio

_COM = threading.local()
_OLE32 = ctypes.WinDLL('ole32')


class _GUID(ctypes.Structure):
    _fields_ = [('Data1', ctypes.c_ulong), ('Data2', ctypes.c_ushort),
                ('Data3', ctypes.c_ushort), ('Data4', ctypes.c_ubyte * 8)]


def _guid(text):
    g = _GUID()
    ctypes.memmove(ctypes.addressof(g), uuid.UUID(text).bytes_le, ctypes.sizeof(g))
    return g


_CLSID_ENUM = _guid('BCDE0395-E52F-467C-8E3D-C4579291692E')   # MMDeviceEnumerator
_IID_ENUM = _guid('A95664D2-9614-4F35-A746-DE8DB63617E6')


def _com_call(ptr, index, restype, argtypes):
    table = ctypes.cast(ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
    return ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(table[index])


def windows_default_output_id():
    """Id of the playback device selected in Windows sound settings, or None."""
    try:
        if not getattr(_COM, 'ready', False):
            hr = _OLE32.CoInitializeEx(None, 0)        # one COM apartment per thread
            if hr < 0 and (hr & 0xFFFFFFFF) != 0x80010106:
                return None
            _COM.ready = True
        enumerator = ctypes.c_void_p()
        hr = _OLE32.CoCreateInstance(ctypes.byref(_CLSID_ENUM), None, 23,
                                    ctypes.byref(_IID_ENUM), ctypes.byref(enumerator))
        if hr < 0 or not enumerator:
            return None
        try:
            device = ctypes.c_void_p()                 # eRender, eConsole: the default output
            hr = _com_call(enumerator, 4, ctypes.HRESULT,
                           [ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)])(
                enumerator, 0, 0, ctypes.byref(device))
            if hr < 0 or not device:
                return None
            try:
                raw = ctypes.c_void_p()
                hr = _com_call(device, 5, ctypes.HRESULT, [ctypes.POINTER(ctypes.c_void_p)])(
                    device, ctypes.byref(raw))
                if hr < 0 or not raw:
                    return None
                try:
                    return ctypes.wstring_at(raw)
                finally:
                    _OLE32.CoTaskMemFree(raw)
            finally:
                _com_call(device, 2, ctypes.c_ulong, [])(device)
        finally:
            _com_call(enumerator, 2, ctypes.c_ulong, [])(enumerator)
    except Exception:
        return None


class AudioEngine:
    """Plays a ring of decoded stereo float32 blocks: static by signal strength, then where the
    listener is (the cabin, outside: muffled and fading with distance, the game in the back)."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.q = collections.deque()
        self.qlen = 0
        self.lock = threading.Lock()
        self.signal = 1.0
        self.buffering = False
        self.active = False
        self.prebuffered = False
        # the game's side (geoRadio.lua 'env'): camera inside the car, its distance to the car (m),
        # pause, ignition level (0 off, 1 accessory, 2 on, None unknown), the game's master volume,
        # where the car is seen from the camera (-1 left .. 1 right), open doors / tailgate / trunk
        self.env = {'inside': True, 'dist': 0.0, 'paused': False, 'ign': None, 'master': 1.0, 'pan': 0.0, 'doors': 0,
                    'level': True}   # level: a map is loaded (not the main menu, not loading)
        self.focused = True
        self.tune_until = 0.0
        self._gain_a = self._gain_n = 0.0
        self._env_gain = 0.0
        self._muffle = 0.0
        self._pan = 0.0
        self._lp = np.zeros(2, dtype=np.float32)
        self._flutter = 1.0
        self._b, self._a = butter(2, MUFFLE_HZ, fs=RATE)
        self._zi = np.zeros((2, 2))
        self.stream = None
        self.output_id = None          # Windows id of the device self.stream plays on
        self._output_check = 0.0
        self._out_lock = threading.Lock()
        self.idle_since = time.time()

    def ensure_stream(self):
        with self._out_lock:
            if self.stream is None:
                self._open_stream()

    def close_stream(self):
        with self._out_lock:
            self._close_stream()

    def follow_output(self):
        """Move an open stream onto the Windows default playback device when the user changes it."""
        now = time.time()
        if now < self._output_check:
            return
        self._output_check = now + 1.0
        current = windows_default_output_id()
        with self._out_lock:
            if self.stream is None:
                if self.active and current:
                    self._open_stream()
                return
            if not current or current == self.output_id:
                return
            log('default audio device changed')
            self._close_stream()
            self._open_stream()

    def _open_stream(self):
        try:                               # PortAudio keeps the device list from startup
            sd._terminate()
            sd._initialize()
            name = sd.query_devices(kind='output')['name']
            self.stream = sd.OutputStream(samplerate=RATE, channels=2, dtype='float32', blocksize=1024,
                                          callback=self._callback)
            self.stream.start()
        except Exception as e:
            self.stream = None
            log('cannot open the default audio device:', e)
            return
        self.output_id = windows_default_output_id() or self.output_id
        log('audio out:', name)

    def _close_stream(self):
        if self.stream is None:
            return
        s, self.stream = self.stream, None
        try:
            s.stop()
            s.close()
        except Exception:
            pass

    def clear(self):
        with self.lock:
            self.q.clear()
            self.qlen = 0
            self.prebuffered = False

    def push(self, arr):
        with self.lock:
            self.q.append(arr)
            self.qlen += len(arr)
            if self.qlen >= RATE:
                self.prebuffered = True

    def buffered_s(self):
        return self.qlen / RATE

    def _pull(self, n):
        out = np.zeros((n, 2), dtype=np.float32)
        got = 0
        with self.lock:
            if not self.prebuffered:
                return out, False
            while got < n and self.q:
                a = self.q[0]
                k = min(n - got, len(a))
                out[got:got + k] = a[:k]
                got += k
                if k == len(a):
                    self.q.popleft()
                else:
                    self.q[0] = a[k:]
                self.qlen -= k
            if got < n:
                self.prebuffered = False   # underrun: wait for a second of audio again
        return out, got > 0

    def _noise(self, n):
        white = np.random.uniform(-1, 1, (n, 2)).astype(np.float32)
        out = np.empty_like(white)
        lp = self._lp
        for i in range(0, n, 64):          # cheap one-pole low-pass per 64-sample chunk: a hiss, not a white glare
            chunk = white[i:i + 64]
            lp = lp * 0.6 + chunk.mean(axis=0) * 0.4
            out[i:i + 64] = chunk * 0.5 + lp
        self._lp = lp
        return out

    def env_target(self):
        """(gain, muffle 0..1, pan -1..1) for where the listener is now."""
        c, e = self.cfg, self.env
        g, m, pan = 1.0, 0.0, 0.0
        if c.get('sound_mode') == 'realistic':
            if c.get('need_ignition') and e.get('ign') is not None and e['ign'] < 1:
                g = 0.0
            if not e.get('inside', True):
                d = max(0.0, float(e.get('dist') or 0) - 2.0)
                opened = min(2, int(e.get('doors') or 0))     # an open door lets the cabin out
                m = 1.0 - 0.35 * opened
                g *= 0.55 * (1 + 0.35 * opened) / (1 + d / 5) * clamp(1 - d / 80)
                pan = clamp(float(e.get('pan') or 0), -1, 1) * clamp(d / 4) * 0.85
        if c.get('mute_paused') and e.get('paused'):
            g = 0.0
        if not e.get('level', True):
            g = 0.0
        if c.get('mute_unfocused') and not self.focused:
            g = 0.0
        if c.get('follow_master'):
            g *= clamp(float(e.get('master', 1.0)))
        return g, m, pan

    def _callback(self, outdata, frames, t, status):
        vol = float(self.cfg.get('volume', 0.6))
        audio, have = self._pull(frames)
        s = self.signal
        if s < 0.5:
            self._flutter = clamp(self._flutter + random.uniform(-0.25, 0.25), 0.2, 1.0)
        else:
            self._flutter = 1.0
        ga = clamp(s / 0.7) * self._flutter if self.active else 0.0
        gn = 0.0
        if self.active:
            gn = clamp(1 - s / 0.8) * 0.35
            if self.buffering or not have:
                gn = max(gn, 0.18)
            if time.time() < self.tune_until:
                ga, gn = 0.0, max(gn, 0.4)
        ramp = np.linspace(0, 1, frames, dtype=np.float32)[:, None]
        a_g = self._gain_a + (ga - self._gain_a) * ramp
        n_g = self._gain_n + (gn - self._gain_n) * ramp
        self._gain_a, self._gain_n = ga, gn
        mix = audio * a_g
        if gn > 0 or n_g[0, 0] > 0:
            mix += self._noise(frames) * n_g

        dt = frames / RATE
        tg, tm, tp = self.env_target()
        eg = self._env_gain + (tg - self._env_gain) * (1 - math.exp(-dt / 0.15))
        mu = self._muffle + (tm - self._muffle) * (1 - math.exp(-dt / 0.25))
        pn = self._pan + (tp - self._pan) * (1 - math.exp(-dt / 0.2))
        wet, self._zi = lfilter(self._b, self._a, mix, axis=0, zi=self._zi)
        m_r = self._muffle + (mu - self._muffle) * ramp
        e_r = self._env_gain + (eg - self._env_gain) * ramp
        p_r = (self._pan + (pn - self._pan) * ramp[:, 0] + 1) * (math.pi / 4)   # equal-power pan law
        self._env_gain, self._muffle, self._pan = eg, mu, pn
        out = (mix * (1 - m_r) + wet.astype(np.float32) * m_r) * e_r
        out[:, 0] *= np.cos(p_r) * math.sqrt(2)
        out[:, 1] *= np.sin(p_r) * math.sqrt(2)
        outdata[:] = np.clip(out * vol, -1, 1)


class IcyReader:
    """A non-seekable file object over an HTTP stream that strips ICY metadata (the song titles)."""

    def __init__(self, url, on_title):
        req = urllib.request.Request(url, headers={'User-Agent': UA, 'Icy-MetaData': '1'})
        self.resp = urllib.request.urlopen(req, timeout=10)
        self.ctype = (self.resp.headers.get('Content-Type') or '').lower()
        self.metaint = int(self.resp.headers.get('icy-metaint') or 0)
        self.left = self.metaint
        self.on_title = on_title
        self.closed = False

    def _read_exact(self, n):
        buf = b''
        while len(buf) < n:
            d = self.resp.read(n - len(buf))
            if not d:
                raise EOFError
            buf += d
        return buf

    def read(self, n=65536):
        if self.closed:
            return b''
        if self.metaint and self.left == 0:
            ln = self._read_exact(1)[0] * 16
            if ln:
                meta = self._read_exact(ln).rstrip(b'\0')
                for enc in ('utf-8', 'cp1251', 'latin-1'):
                    try:
                        text = meta.decode(enc)
                        break
                    except UnicodeDecodeError:
                        continue
                m = re.search(r"StreamTitle='(.*?)';", text, re.S)
                if m:
                    self.on_title(m.group(1).strip())
            self.left = self.metaint
        if self.metaint:
            n = min(n, self.left)
        d = self.resp.read1(n)
        if self.metaint:
            self.left -= len(d)
        return d

    def close(self):
        self.closed = True
        try:
            self.resp.close()
        except Exception:
            pass


class Player:
    def __init__(self, engine, on_change):
        self.engine = engine
        self.on_change = on_change
        self.station = None
        self.status = 'stopped'     # stopped | buffering | playing | error
        self.title = ''
        self.error = ''
        self._stop = None
        self._thread = None

    def play(self, station):
        self.stop(silent=True)
        self.engine.ensure_stream()
        self.station, self.title, self.error = station, '', ''
        self.status = 'buffering'
        stop = self._stop = threading.Event()
        self.engine.clear()
        self.engine.active = True
        self.engine.buffering = True
        self.engine.signal = station.signal
        self._thread = threading.Thread(target=self._run, args=(station, stop), daemon=True)
        self._thread.start()
        self.on_change()

    def stop(self, silent=False):
        if self._stop:
            self._stop.set()
        self._stop = None
        self.engine.active = False
        self.engine.clear()
        self.engine.idle_since = time.time()
        self.station, self.status, self.title = None, 'stopped', ''
        if not silent:
            self.on_change()

    def _set_title(self, t):
        if t != self.title:
            self.title = t
            self.on_change()

    def _open(self, url):
        hls = '.m3u8' in url.lower()
        if not hls:
            try:
                rd = IcyReader(url, self._set_title)
                if 'mpegurl' not in rd.ctype:
                    return av.open(rd, mode='r', options={'probesize': '131072'}), rd
                rd.close()
            except Exception as e:
                log('direct HTTP failed (%s), letting ffmpeg open it' % e)
        return av.open(url, mode='r', timeout=12,
                       options={'reconnect': '1', 'reconnect_streamed': '1', 'reconnect_delay_max': '5'}), None

    def _run(self, station, stop):
        tries = 0
        while not stop.is_set() and tries < 4:
            tries += 1
            container = reader = None
            try:
                container, reader = self._open(station.url)
                stream = container.streams.audio[0]
                resampler = av.AudioResampler(format='flt', layout='stereo', rate=RATE)
                for frame in container.decode(stream):
                    if stop.is_set():
                        break
                    for rf in resampler.resample(frame):
                        self.engine.push(rf.to_ndarray().reshape(-1, 2).astype(np.float32, copy=False))
                    if self.status != 'playing' and self.engine.prebuffered:
                        self.status, self.engine.buffering = 'playing', False
                        tries = 0
                        self.on_change()
                    while self.engine.buffered_s() > 6 and not stop.is_set():
                        time.sleep(0.05)
                if not stop.is_set():
                    raise EOFError('stream ended')
            except Exception as e:
                if stop.is_set():
                    break
                self.error = str(e) or e.__class__.__name__
                log('stream error (%s): %s' % (station.name, self.error))
                self.status, self.engine.buffering = 'buffering', True
                self.on_change()
                time.sleep(min(8, 1.5 * tries))
            finally:
                for c in (container, reader):
                    try:
                        if c:
                            c.close()
                    except Exception:
                        pass
        if not stop.is_set():
            self.status, self.engine.active = 'error', False
            self.on_change()


# ---------------------------------------------------------------- the server the game talks to

TEXT = {
    'ru': {
        'weak': 'Сигнал «%s» слабеет. Рядом ловятся:',
        'lost': 'Станция «%s» пропала из эфира.',
        'region': 'Регион: %s. Местных станций: %d',
        'resume_far': 'Последняя станция «%s» здесь не ловится.',
    },
    'en': {
        'weak': '"%s" is fading. Stations around here:',
        'lost': '"%s" went off the air.',
        'region': 'Entering %s: %d local stations',
        'resume_far': 'The last station, "%s", is out of range here.',
    },
}


class App:
    def __init__(self):
        self.cfg = load_config()
        self.finder = Finder(self.cfg)
        self.finder.on_region_change = self._region_changed
        self.engine = AudioEngine(self.cfg)
        self.player = Player(self.engine, self._mark_dirty)
        self.lock = threading.RLock()
        self.clients = []
        self.clients_lock = threading.Lock()
        self.pos = None
        self.pending_pos = None
        self.dirty = True
        self.weak_warned = False
        self.lost_since = None
        self.no_game_since = time.time()
        self.resume_pending = False
        self.teleported = False
        self.no_level_since = None

    def t(self, key):
        return TEXT.get(self.cfg.get('lang'), TEXT['ru'])[key]

    def _mark_dirty(self):
        self.dirty = True

    def send(self, msg):
        line = (json.dumps(msg, ensure_ascii=False) + '\n').encode('utf-8')
        with self.clients_lock:
            for c in list(self.clients):
                try:
                    c.sendall(line)
                except OSError:
                    self.clients.remove(c)

    def notice(self, text, suggestions=()):
        log('notice:', text, ', '.join(s.name for s in suggestions))
        self.send({'type': 'notice', 'text': text,
                   'suggestions': [{'id': s.id, 'name': s.name} for s in suggestions]})

    def _region_changed(self, region):
        n = sum(1 for s in self.finder.listed() if s.kind != 'national')
        place = ', '.join(x for x in (region.get('city'), region.get('state')) if x) or region.get('country', '?')
        with self.lock:
            # the radio was on before a teleport: it stays on, with the strongest station of the new place
            if self.teleported and self.player.station is None and self.cfg.get('was_playing'):
                good = self.suggestions(None)
                if good:
                    self.play(good[0])
            self.teleported = False
            self.notice(self.t('region') % (place, n), () if self.player.station else self.suggestions(None))
        self.dirty = True

    def suggestions(self, exclude):
        cur = self.player.station if self.player.station and self.player.station.id == exclude else None
        tags = set(cur.tags) if cur else set()
        good = [s for s in self.finder.listed() if s.id != exclude and s.signal >= 0.9]
        good.sort(key=lambda s: (-len(tags & set(s.tags)), s.kind != 'local', -s.votes))
        return good[:3]

    def state_msg(self):
        p = self.player
        st = p.station
        favs = self.cfg['favorites']
        settings = {k: self.cfg[k] for k in SETTABLE}
        return {
            'type': 'state', 'version': VERSION,
            'region': self.finder.region, 'lookup': self.finder.status,
            'volume': self.cfg['volume'], 'radius': self.cfg['radius_km'], 'lang': self.cfg['lang'],
            'settings': settings, 'focused': self.engine.focused,
            'stations': [{'id': s.id, 'name': s.name, 'kind': s.kind, 'signal': round(s.signal, 2),
                          'dist': s.dist and round(s.dist, 1), 'tags': ', '.join(s.tags), 'freq': s.freq,
                          'codec': s.codec, 'bitrate': s.bitrate, 'state': s.state, 'fav': s.id in favs}
                         for s in self.finder.listed()],
            'favorites': [{'id': k, 'name': (v.get('name') or '?').strip(), 'tags': v.get('tags') or '',
                           'state': v.get('state') or '', 'cc': v.get('countrycode') or '',
                           'freq': parse_freq(v.get('name'), v.get('tags'))}
                          for k, v in sorted(favs.items(), key=lambda kv: (kv[1].get('name') or '').lower())],
            'songs': self.cfg['songs'][:100],
            'last': ((self.cfg.get('last_station') or {}).get('raw') or {}).get('name'),
            'playing': st and {'id': st.id, 'name': st.name, 'status': p.status, 'title': p.title,
                               'signal': round(st.signal, 2), 'error': p.error, 'online': st.kind == 'online',
                               'fav': st.id in favs, 'freq': st.freq,
                               'saved': bool(p.title) and any(s.get('title') == p.title for s in self.cfg['songs'][:20])},
        }

    def seek(self, up):
        """<< >>: tune to the next station by frequency (stations without one after them, by name), with static between."""
        cur = self.player.station
        lst = [s for s in self.finder.listed() if s.signal >= 0.2]
        if cur and cur.kind == 'online':
            favs = self.cfg['favorites']
            lst = [Station(raw, 'online') for raw in favs.values()]
        if not lst:
            return
        lst.sort(key=lambda s: (s.freq is None, s.freq or 0, s.name.lower()))
        ids = [s.id for s in lst]
        i = ids.index(cur.id) if cur and cur.id in ids else (-1 if up else 0)
        nxt = lst[(i + (1 if up else -1)) % len(lst)]
        self.engine.tune_until = time.time() + TUNE_S
        self.play(nxt)

    def write_songs_txt(self):
        try:
            with open(SONGS_TXT, 'w', encoding='utf-8') as f:
                for s in self.cfg['songs']:
                    f.write('%s  |  %s  |  %s  |  %s\n' % (s.get('title'), s.get('station'), s.get('place'), s.get('time')))
        except Exception as e:
            log('cannot write saved_songs.txt:', e)

    def save_song(self):
        p = self.player
        if not p.station or not p.title:
            return
        songs = self.cfg['songs']
        if any(s.get('title') == p.title for s in songs[:20]):
            return
        r = self.finder.region
        place = ', '.join(x for x in (r.get('city_local') or r.get('city'), r.get('state_local') or r.get('state'),
                                      r.get('country_local') or r.get('country')) if x)
        songs.insert(0, {'title': p.title, 'station': p.station.name, 'place': place,
                         'time': time.strftime('%Y-%m-%d %H:%M')})
        del songs[MAX_SONGS:]
        save_config(self.cfg)
        self.write_songs_txt()
        log('song saved:', p.title)

    def play(self, st):
        log('play:', st.name, st.url)
        self.weak_warned, self.lost_since = False, None
        self.finder.keep = st.id
        self.player.play(st)
        self.cfg['last_station'] = {'raw': st.raw, 'online': st.kind == 'online'}
        self.cfg['was_playing'] = True
        save_config(self.cfg)
        threading.Thread(target=self.finder.rb.click, args=(st.id,), daemon=True).start()

    def handle(self, msg):
        with self.lock:
            self._handle(msg)
        self.dirty = True

    def _handle(self, msg):
        t = msg.get('type')
        if t == 'pos':
            self.on_pos(float(msg['lat']), float(msg['lon']))
        elif t == 'env':
            e = self.engine.env
            e['inside'] = bool(msg.get('inside', True))
            e['dist'] = float(msg.get('dist') or 0)
            e['paused'] = bool(msg.get('paused'))
            e['master'] = clamp(float(msg.get('master', 1.0)))
            e['pan'] = clamp(float(msg.get('pan') or 0), -1, 1)
            e['doors'] = int(msg.get('doors') or 0)
            e['level'] = bool(msg.get('level', True))
            ign = msg.get('ign')
            e['ign'] = None if ign is None else int(ign)
        elif t == 'play':
            if msg.get('online'):
                raw = self.cfg['favorites'].get(msg.get('id'))
                if raw:
                    self.play(Station(raw, 'online'))
            else:
                st = self.finder.get(msg.get('id'))
                if st:
                    self.play(st)
        elif t == 'stop':
            self.player.stop()
            self.cfg['was_playing'] = False
            save_config(self.cfg)
        elif t == 'resume':
            self.resume_last()
        elif t in ('next', 'prev'):
            self.seek(t == 'next')
        elif t == 'save_song':
            self.save_song()
        elif t == 'del_song':
            i = int(msg.get('i', -1))
            if 0 <= i < len(self.cfg['songs']):
                del self.cfg['songs'][i]
                save_config(self.cfg)
                self.write_songs_txt()
        elif t == 'fav':
            sid = msg.get('id')
            favs = self.cfg['favorites']
            if sid in favs:
                del favs[sid]
            else:
                st = self.finder.get(sid) or (self.player.station if self.player.station and self.player.station.id == sid else None)
                if st:
                    favs[sid] = st.raw
            save_config(self.cfg)
        elif t == 'volume':
            self.cfg['volume'] = clamp(float(msg.get('v', 0.6)))
            save_config(self.cfg)
        elif t == 'radius':
            self.cfg['radius_km'] = max(10, min(300, float(msg.get('km', 80))))
            save_config(self.cfg)
            if self.pos:
                self.finder.maybe_lookup(*self.pos, force=True)
        elif t == 'lang':
            self.cfg['lang'] = 'en' if msg.get('lang') == 'en' else 'ru'
            save_config(self.cfg)
        elif t == 'set' and msg.get('key') in SETTABLE:
            key, val = msg['key'], msg.get('value')
            if key == 'sound_mode':
                val = 'practical' if val == 'practical' else 'realistic'
            else:
                val = bool(val)
            self.cfg[key] = val
            save_config(self.cfg)
            if key == 'autostart':
                apply_autostart(val)
        elif t == 'refresh' and self.pos:
            self.finder.maybe_lookup(*self.pos, force=True)

    def on_pos(self, lat, lon):
        # A floating-origin rebase in BeamNG.Earth can give one odd sample: a jump of kilometres
        # in a second is believed only when the next sample agrees (a teleport).
        if self.pos and haversine_km(lat, lon, *self.pos) > 2.0:
            if not self.pending_pos or haversine_km(lat, lon, *self.pending_pos) > 0.5:
                self.pending_pos = (lat, lon)
                return
            self.on_teleport()
        self.pending_pos = None
        self.pos = (lat, lon)
        with self.finder.lock:
            self.finder.pos = self.pos
            self.finder.keep = self.player.station.id if self.player.station else None
            self.finder.update_signal(lat, lon)
        self.finder.maybe_lookup(lat, lon)
        self.check_signal()

    def on_teleport(self):
        st = self.player.station
        log('teleport: forgetting the stations of the old place')
        if st and st.kind != 'online':
            self.player.stop(silent=True)
            self.teleported = True
        self.finder.keep = self.player.station.id if self.player.station else None
        self.finder.teleport()
        self.weak_warned, self.lost_since = False, None

    def resume_last(self):
        """The play button: the last station again (it may be out of range here)."""
        last = self.cfg.get('last_station') or {}
        raw = last.get('raw') or {}
        if not raw.get('stationuuid'):
            good = self.suggestions(None)
            if good:
                self.play(good[0])
            return
        if last.get('online'):
            self.play(Station(raw, 'online'))
            return
        st = self.finder.get(raw['stationuuid'])
        if st and st.signal > 0.05:
            self.play(st)
        else:
            self.notice(self.t('resume_far') % (raw.get('name') or '?').strip(), self.suggestions(None))

    def check_signal(self):
        st = self.player.station
        if not st:
            return
        self.engine.signal = st.signal
        if st.kind == 'online':
            return
        if st.signal < WEAK_SIGNAL and not self.weak_warned:
            self.weak_warned = True
            self.notice(self.t('weak') % st.name, self.suggestions(st.id))
        elif st.signal >= WEAK_SIGNAL + 0.15:
            self.weak_warned = False
        if st.signal <= 0:
            self.lost_since = self.lost_since or time.time()
            if time.time() - self.lost_since > LOST_STOP_S:
                name = st.name
                self.player.stop()
                self.notice(self.t('lost') % name, self.suggestions(None))
        else:
            self.lost_since = None

    def try_resume(self):
        last = self.cfg.get('last_station')
        if not self.cfg.get('auto_resume') or not last or self.player.station or not self.cfg.get('was_playing'):
            self.resume_pending = False
            return
        if not self.engine.env.get('level', True):
            return                     # wait until a map is loaded
        raw = last.get('raw') or {}
        if last.get('online'):
            self.resume_pending = False
            self.play(Station(raw, 'online'))
            return
        if self.finder.status != 'ok' or not self.pos:
            return                     # wait for the first lookup here
        self.resume_pending = False
        st = self.finder.get(raw.get('stationuuid'))
        if st and st.signal > 0.3:
            self.play(st)
        else:
            self.notice(self.t('resume_far') % (raw.get('name') or '?').strip(), self.suggestions(None))

    def serve_client(self, conn):
        with self.clients_lock:
            self.clients.append(conn)
        log('game connected')
        with self.lock:
            self.engine.env['level'] = False   # until the game says a map is loaded
            self.resume_pending = self.player.station is None
        self.dirty = True
        buf = b''
        try:
            while True:
                d = conn.recv(65536)
                if not d:
                    break
                buf += d
                while b'\n' in buf:
                    line, buf = buf.split(b'\n', 1)
                    if line.strip():
                        try:
                            self.handle(json.loads(line.decode('utf-8', 'replace')))
                        except Exception:
                            log('bad message:', line[:200], traceback.format_exc(limit=1))
        except OSError:
            pass
        finally:
            with self.clients_lock:
                if conn in self.clients:
                    self.clients.remove(conn)
                if not self.clients:
                    self.no_game_since = time.time()
            conn.close()
            log('game disconnected')

    def pump(self):
        last = 0
        while True:
            time.sleep(0.25)
            now = time.time()
            self.engine.follow_output()
            exe = foreground_exe()
            focused = exe.startswith('beamng') if exe else True
            if focused != self.engine.focused:
                self.engine.focused = focused
                self.dirty = True
            with self.lock:
                with self.clients_lock:
                    has_game = bool(self.clients)
                if not has_game and self.player.station and now - self.no_game_since > GAME_GONE_STOP_S:
                    log('the game is gone: stopping')
                    self.player.stop()
                if self.engine.env.get('level', True):
                    self.no_level_since = None
                else:
                    self.no_level_since = self.no_level_since or now
                    if self.player.station and now - self.no_level_since > GAME_GONE_STOP_S:
                        log('no map loaded: stopping until one is')
                        self.player.stop()
                        self.resume_pending = True
                if has_game and self.resume_pending:
                    self.try_resume()
                if not self.player.station and self.engine.stream is not None \
                        and now - self.engine.idle_since > STREAM_IDLE_CLOSE_S:
                    self.engine.close_stream()
            if self.dirty or now - last > 1.0:
                self.dirty = False
                last = now
                self.send(self.state_msg())

    def run(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):   # Windows' SO_REUSEADDR would let a second helper share the port
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            srv.bind((HOST, PORT))
        except OSError:
            log('port %d is busy: is the helper already running?' % PORT)
            sys.exit(1)
        srv.listen(4)
        if self.cfg.get('autostart'):
            apply_autostart(True)      # (the script may have moved since)
        threading.Thread(target=self.pump, daemon=True).start()
        log('Geo Radio helper %s on %s:%d' % (VERSION, HOST, PORT))
        log('Waiting for BeamNG.drive (load the BeamNG.Earth level, the Geo Radio window connects by itself)...')
        while True:
            conn, _ = srv.accept()
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            threading.Thread(target=self.serve_client, args=(conn,), daemon=True).start()


def selftest(lat, lon):
    """python georadio_helper.py --test 55.75 37.62 : look up and play the first station for 15 s."""
    app = App()
    app.on_pos(lat, lon)
    while app.finder.busy or app.finder.status == 'idle':
        time.sleep(0.2)
    lst = app.finder.listed()
    print(app.finder.region)
    for s in lst[:15]:
        print('%4.0f%%  %-9s %6s km  %s' % (s.signal * 100, s.kind, s.dist and round(s.dist), s.name))
    if lst:
        app.handle({'type': 'play', 'id': lst[0].id})
        for _ in range(30):
            time.sleep(0.5)
            print(app.player.status, app.player.title, app.player.error, '%.1fs' % app.engine.buffered_s())


def set_autostart_cli(on):
    """--autostart on|off: from a .bat, before the helper ever ran (the in-game switch needs it running)."""
    cfg = load_config()
    cfg['autostart'] = on
    save_config(cfg)
    apply_autostart(on)
    if on:
        import subprocess
        pyw = os.path.join(os.path.dirname(sys.executable), 'pythonw.exe')
        subprocess.Popen([pyw if os.path.exists(pyw) else sys.executable, os.path.abspath(__file__)], cwd=HERE,
                         creationflags=0x00000008 | 0x00000200)   # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        log('the helper is running in the background now')


if __name__ == '__main__':
    if len(sys.argv) >= 4 and sys.argv[1] == '--test':
        selftest(float(sys.argv[2]), float(sys.argv[3]))
    elif len(sys.argv) >= 3 and sys.argv[1] == '--autostart':
        set_autostart_cli(sys.argv[2] == 'on')
    else:
        try:
            App().run()
        except KeyboardInterrupt:
            pass
