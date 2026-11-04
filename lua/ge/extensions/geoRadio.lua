-- Geo Radio: real radio stations of the place you drive in BeamNG.Earth.
-- The game cannot stream audio from the internet, so the helper program (helper/georadio_helper.py)
-- finds the stations and plays them; this extension sends it the car's lat/lon (BeamNG.Earth's anchor)
-- and where the camera is, over a local TCP socket, and draws the station picker.
-- Wire: one JSON object per line. Plain strings go through TextUnformatted (Text treats % as a format).

local M = {}

local HOST, PORT = '127.0.0.1', 47620
local RECONNECT_S = 3
local POS_EVERY_S = 1
local ENV_EVERY_S = 0.1
local VEH_EVERY_S = 1
local SCREEN_EVERY_S = 1
local UI_FILE = 'settings/geoRadio_ui.json'

local socket, sock = nil, nil
local connState = 'disconnected'   -- 'disconnected' | 'connecting' | 'connected'
local reconnectTimer = 0
local rxbuf, txbuf = '', ''
local posTimer, envTimer, vehTimer, envHeartbeat, screenTimer = 0, 0, 0, 0, 0

local data = nil        -- the helper's last 'state' message
local notice = nil      -- { text, suggestions = {{id, name}} }
local lastLat, lastLon = nil, nil
local noEarth = false
local ignition = nil    -- the player vehicle's electrics.values.ignitionLevel (geoRadio.onVehState)
local doorsOpen = 0     -- its open doors / tailgate / trunk
local lastEnv = nil

local ui = { visible = true, compact = false, lang = 'en', w = 430, h = 640 }   -- w, h: the full window's size
local restoreSize = false   -- leaving compact mode: give the window its full size back
local sizeDirty = false     -- the full window was resized: saved once the mouse is released
local levelReady = false    -- a map is loaded and started (not the main menu, not a loading screen)
local im = nil
local openPtr, volPtr, radiusPtr, filterBuf = nil, nil, nil, nil
local boolPtrs = {}

-- ---------------------------------------------------------------- texts

local L = {
  ru = {
    title = 'Гео-Радио', helperOff = 'Помощник не запущен. Запустите helper\\start_georadio.bat из папки мода, а чтобы он всегда стартовал сам - один раз helper\\enable_autostart.bat.',
    connecting = 'Подключение к помощнику...', noEarth = 'Нет координат: загрузите уровень BeamNG.Earth.',
    region = 'Где вы: ', searching = 'Поиск станций...', nothing = 'Ничего не играет', noTitle = 'Станция не передаёт название песни',
    stop = 'Стоп', volume = 'Громкость', radius = 'Радиус поиска (км)', signal = 'Сигнал', online = 'онлайн',
    buffering = 'подключение...', error = 'ошибка потока', dismiss = 'Скрыть', refresh = 'Обновить',
    nearby = 'Рядом (%d)', favorites = 'Избранное (%d)', none = 'Пока станций не найдено.',
    noFavs = 'Избранного пока нет. Нажмите звёздочку у станции.',
    favsHint = 'Избранное играет из любой точки мира, без помех.',
    filter = 'Поиск: название, жанр...', settings = 'Настройки',
    soundMode = 'Звук:', realistic = 'Реалистичный', practical = 'Практичный',
    realisticTip = 'Громко в салоне. Снаружи приглушённо, тише с расстоянием, слышно с той стороны, где машина; открытые двери выпускают звук.',
    practicalTip = 'Всегда одинаково, где бы ни была камера.',
    mute_unfocused = 'Без звука, когда игра свёрнута или не в фокусе',
    mute_paused = 'Без звука на паузе',
    follow_master = 'Учитывать общую громкость игры',
    need_ignition = 'Радио работает только при включённом зажигании',
    need_ignitionTip = 'Только в реалистичном режиме.',
    auto_resume = 'Включать последнюю станцию при запуске',
    car_screen = 'Показывать станцию и песню на экране машины',
    car_screenTip = 'На экране навигации в машинах, где он есть.',
    autostart = 'Запускать помощник вместе с Windows (скрыто)',
    autostartTip = 'Помощник будет тихо ждать игру в фоне.',
    play = 'Играть', pause = 'Пауза', playTip = 'Включить: ', playNear = 'Включить станцию поблизости',
    compact = 'Свернуть', expand = 'Развернуть', favAdd = 'В избранное', favDel = 'Убрать из избранного',
    prevTip = 'Настройка вниз по частоте', nextTip = 'Настройка вверх по частоте',
    saveSong = 'Сохранить песню в список', songSaved = 'Песня уже в списке',
    songs = 'Сохранённые песни (%d)', noSongs = 'Пусто. Нажмите закладку под названием песни, чтобы сохранить её.',
    copy = 'Копировать', youtube = 'YouTube', delete = 'Удалить', openFile = 'Открыть файл',
    copied = 'Скопировано: ', muted = 'звук приглушён: игра не в фокусе',
    hotkeys = 'Горячие клавиши', change = 'Изменить', clear = 'Сбросить', pressKeys = 'Нажмите сочетание... (Esc - отмена)',
    unbound = '(нет)', hotkeysTip = 'Лучше с Ctrl / Alt / Shift: одиночная клавиша может быть занята игрой. То же есть в Настройки > Управление.',
    act = { toggle = 'Показать / скрыть окно', prev = 'Станция вниз (<<)', next = 'Станция вверх (>>)', stop = 'Играть / пауза',
      favorite = 'В избранное', saveSong = 'Сохранить песню', compact = 'Компактный режим' },
    kind = { ['local'] = 'рядом', regional = 'регион', national = 'страна' },
    km = ' км',
  },
  en = {
    title = 'Geo Radio', helperOff = 'The helper is not running. Start helper\\start_georadio.bat in the mod folder; run helper\\enable_autostart.bat once to have it start by itself.',
    connecting = 'Connecting to the helper...', noEarth = 'No coordinates: load the BeamNG.Earth level.',
    region = 'You are in: ', searching = 'Looking for stations...', nothing = 'Nothing playing', noTitle = 'The station sends no song title',
    stop = 'Stop', volume = 'Volume', radius = 'Search radius (km)', signal = 'Signal', online = 'online',
    buffering = 'connecting...', error = 'stream error', dismiss = 'Dismiss', refresh = 'Refresh',
    nearby = 'Nearby (%d)', favorites = 'Favorites (%d)', none = 'No stations found yet.',
    noFavs = 'No favorites yet. Click the star next to a station.',
    favsHint = 'Favorites play anywhere in the world, without static.',
    filter = 'Search: name, genre...', settings = 'Settings',
    soundMode = 'Sound:', realistic = 'Realistic', practical = 'Practical',
    realisticTip = 'Loud in the cabin. Outside: muffled, quieter with distance, coming from the side the car is on; open doors let the sound out.',
    practicalTip = 'Always the same, wherever the camera is.',
    mute_unfocused = 'Mute when the game is minimized or not focused',
    mute_paused = 'Mute while paused',
    follow_master = 'Follow the game master volume',
    need_ignition = 'The radio needs the ignition on',
    need_ignitionTip = 'Realistic mode only.',
    auto_resume = 'Resume the last station on start',
    car_screen = 'Show the station and song on the car screen',
    car_screenTip = 'On the navigation screen of the cars that have one.',
    autostart = 'Start the helper with Windows (hidden)',
    autostartTip = 'The helper waits for the game quietly in the background.',
    play = 'Play', pause = 'Pause', playTip = 'Play: ', playNear = 'Play a station nearby',
    compact = 'Compact', expand = 'Expand', favAdd = 'Add to favorites', favDel = 'Remove from favorites',
    prevTip = 'Tune down', nextTip = 'Tune up',
    saveSong = 'Save the song to the list', songSaved = 'The song is in the list',
    songs = 'Saved songs (%d)', noSongs = 'Empty. Click the bookmark under the song title to save it.',
    copy = 'Copy', youtube = 'YouTube', delete = 'Delete', openFile = 'Open file',
    copied = 'Copied: ', muted = 'muted: the game is not focused',
    hotkeys = 'Hotkeys', change = 'Change', clear = 'Clear', pressKeys = 'Press a key combination... (Esc cancels)',
    unbound = '(none)', hotkeysTip = 'Use Ctrl / Alt / Shift: a single key may already do something in the game. Also in Options > Controls.',
    act = { toggle = 'Show / hide window', prev = 'Tune down (<<)', next = 'Tune up (>>)', stop = 'Play / pause',
      favorite = 'Add to favorites', saveSong = 'Save the song', compact = 'Compact window' },
    kind = { ['local'] = 'near', regional = 'region', national = 'country' },
    km = ' km',
  },
}
local function tr(k) return (L[ui.lang] or L.en)[k] end

-- ---------------------------------------------------------------- the window's own state

local function loadUi()
  local ok, t = pcall(jsonReadFile, UI_FILE)
  if ok and type(t) == 'table' then
    for k, v in pairs(t) do if ui[k] ~= nil then ui[k] = v end end
  end
  if ui.lang ~= 'ru' then ui.lang = 'en' end
  ui.w, ui.h = math.max(300, tonumber(ui.w) or 430), math.max(250, tonumber(ui.h) or 640)
  restoreSize = not ui.compact
  levelReady = type(getMissionFilename) == 'function' and getMissionFilename() ~= ''
end

local function saveUi() pcall(jsonWriteFile, UI_FILE, ui, true) end

-- ---------------------------------------------------------------- connection

local function closeSock()
  if sock then pcall(sock.close, sock) end
  sock, connState, reconnectTimer, rxbuf, txbuf, lastEnv = nil, 'disconnected', RECONNECT_S, '', '', nil
end

local function flush()
  if connState ~= 'connected' or txbuf == '' then return end
  local last, err, partial = sock:send(txbuf)
  if last then
    txbuf = ''
  elseif err == 'timeout' then
    txbuf = txbuf:sub((partial or 0) + 1)
  else
    closeSock()
  end
end

local function send(msg)
  if connState ~= 'connected' then return end
  txbuf = txbuf .. jsonEncode(msg) .. '\n'
  flush()
end

local function onMessage(msg)
  if msg.type == 'state' then
    data = msg
  elseif msg.type == 'notice' then
    notice = { text = msg.text or '', suggestions = msg.suggestions or {} }
    local names = {}
    for _, s in ipairs(notice.suggestions) do names[#names + 1] = s.name end
    ui_message(notice.text .. (#names > 0 and (' ' .. table.concat(names, ', ')) or ''), 8, 'geoRadio', 'radio')
  end
end

local function pumpSocket(dt)
  if connState == 'disconnected' then
    reconnectTimer = reconnectTimer - dt
    if reconnectTimer > 0 then return end
    socket = socket or require('socket')
    sock = socket.tcp()
    sock:settimeout(0)
    local ok, err = sock:connect(HOST, PORT)
    if not ok and err ~= 'timeout' and err ~= 'Operation already in progress' then closeSock() return end
    connState, reconnectTimer = 'connecting', 2
    return
  end
  if connState == 'connecting' then
    -- luasocket: a non-blocking connect completes when the peer is known
    if sock:getpeername() then
      connState = 'connected'
      pcall(sock.setoption, sock, 'tcp-nodelay', true)
      send({ type = 'hello', game = 'BeamNG.drive' })
      send({ type = 'lang', lang = ui.lang })
      if lastLat then send({ type = 'pos', lat = lastLat, lon = lastLon }) end
    else
      reconnectTimer = reconnectTimer - dt
      if reconnectTimer <= 0 then closeSock() end
    end
    return
  end
  flush()
  for _ = 1, 16 do
    local chunk, err, partial = sock:receive(65536)
    chunk = chunk or partial
    if chunk and #chunk > 0 then rxbuf = rxbuf .. chunk end
    if err == 'closed' then closeSock() return end
    if not chunk or #chunk == 0 or err == 'timeout' then break end
  end
  while true do
    local nl = rxbuf:find('\n', 1, true)
    if not nl then break end
    local line = rxbuf:sub(1, nl - 1)
    rxbuf = rxbuf:sub(nl + 1)
    local ok, msg = pcall(jsonDecode, line)
    if ok and type(msg) == 'table' then onMessage(msg) end
  end
end

-- ---------------------------------------------------------------- the car and the listener

local function playerLatLon()
  local core = rawget(_G, 'beamngEarth_core')
  local st = core and core._state and core._state()
  local a = st and st.anchor
  if not a or not st.inOurLevel then return nil end
  local veh = getPlayerVehicle(0)
  local pos = veh and veh:getPosition() or (core_camera and core_camera.getPosition())
  if not pos then return nil end
  local ok, lat, lon = pcall(a.worldToGeodetic, a, pos.x, pos.y, pos.z)
  if ok and tonumber(lat) and tonumber(lon) then return lat, lon end
end

-- Called from the vehicle's Lua (VEH_POLL below).
function M.onVehState(ign, doors)
  ignition = tonumber(ign)
  doorsOpen = tonumber(doors) or 0
end

-- Runs in the player vehicle's Lua. Doors are couplers; vehicles name their "open" signal <part>Coupler_notAttached.
local VEH_POLL = [[
local o = 0
for k, v in pairs(electrics.values) do
  if type(k) == 'string' and k:find('Coupler_notAttached', 1, true) and (v == 1 or v == true) then
    local l = k:lower()
    if l:find('door', 1, true) or l:find('tailgate', 1, true) or l:find('hatch', 1, true) or l:find('trunk', 1, true) then o = o + 1 end
  end
end
obj:queueGameEngineLua('if geoRadio then geoRadio.onVehState(' .. tostring(electrics.values.ignitionLevel) .. ',' .. o .. ') end')
]]

local function pollVehicle()
  local veh = getPlayerVehicle(0)
  if not veh then ignition, doorsOpen = nil, 0 return end
  veh:queueLuaCommand(VEH_POLL)
end

local function currentEnv()
  local veh = getPlayerVehicle(0)
  local camPos = core_camera and core_camera.getPosition()
  local inside, dist, pan = true, 0, 0
  if veh and camPos then
    inside = core_camera.isCameraInside(0, camPos) == 1
    local vpos = veh:getPosition()
    dist = camPos:distance(vpos)
    if dist > 0.5 then
      local right = core_camera.getQuat() * vec3(1, 0, 0)
      pan = (vpos - camPos):normalized():dot(right)
    end
  end
  local paused = simTimeAuthority and simTimeAuthority.getPause() or false
  local okM, master = pcall(settings.getValue, 'AudioMasterVol')
  master = okM and tonumber(master) or 1
  return { type = 'env', inside = inside, dist = math.floor(dist * 2 + 0.5) / 2, paused = paused == true,
    master = math.floor(master * 100 + 0.5) / 100, ign = veh and ignition or nil,
    pan = math.floor(pan * 10 + 0.5) / 10, doors = veh and doorsOpen or 0, level = levelReady }
end

local function envChanged(a, b)
  return not b or a.level ~= b.level or a.inside ~= b.inside or a.paused ~= b.paused or a.ign ~= b.ign or a.doors ~= b.doors
    or math.abs(a.dist - b.dist) >= 0.5 or math.abs(a.master - b.master) >= 0.01 or math.abs(a.pan - b.pan) >= 0.1
end

-- ---------------------------------------------------------------- the car's navigation screen

-- Vehicles register their navigation WebViews through ui_uinavi.requestVehicleDashboardMap (BeamNavigator,
-- gauge clusters). BeamNG.Earth wraps it every frame when it is not on top; it wraps ours once and stays.
local screens = {}      -- [vehId \0 tag] = { vid, tag }
local navHooked = nil

local function hookNav()
  local all = rawget(_G, 'extensions')
  local nav = type(all) == 'table' and all.ui_uinavi or nil
  if not nav or type(nav.requestVehicleDashboardMap) ~= 'function' or nav == navHooked then return end
  local inner = nav.requestVehicleDashboardMap
  nav.requestVehicleDashboardMap = function(tag, initmap, vehId)
    local r = inner(tag, initmap, vehId)
    if type(tag) == 'string' and tag ~= '' then
      local id = tonumber(vehId) or be:getPlayerVehicleID(0)
      screens[tostring(id) .. '\0' .. tag] = { vid = id, tag = tag }
    end
    return r
  end
  navHooked = nav
end

local OSD_JS = [[(function(d){var e=document.getElementById('geoRadioOSD');if(!e){e=document.createElement('div');e.id='geoRadioOSD';]]
  .. [[e.style.cssText='position:fixed;left:0;right:0;bottom:0;z-index:2147483647;background:rgba(0,0,0,0.72);color:#fff;font-family:sans-serif;padding:1.5vh 3vw;pointer-events:none;';]]
  .. [[var a=document.createElement('div');a.style.cssText='font-weight:bold;font-size:10vh;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:#ffcb3d';]]
  .. [[var b=document.createElement('div');b.style.cssText='font-size:9vh;white-space:nowrap;overflow:hidden;text-overflow:ellipsis';]]
  .. [[e.appendChild(a);e.appendChild(b);(document.body||document.documentElement).appendChild(e);}]]
  .. [[e.style.display=d.show?'block':'none';e.children[0].textContent=d.a||'';e.children[1].textContent=d.b||'';})(]]

local function freqLabel(f) return f and string.format('%.1f FM  ', f) or '' end

local function updateScreens()
  local pid = be:getPlayerVehicleID(0)
  local p = data and data.playing
  local s = data and data.settings or {}
  local show = connState == 'connected' and p ~= nil and s.car_screen ~= false
  if show and s.sound_mode ~= 'practical' and s.need_ignition and ignition and ignition < 1 then show = false end
  local js = OSD_JS .. jsonEncode({ show = show, a = p and (freqLabel(p.freq) .. p.name) or '', b = p and p.title or '' }) .. ')'
  for key, rec in pairs(screens) do
    local veh = be:getObjectByID(rec.vid)
    if not veh then
      screens[key] = nil
    elseif rec.vid == pid or rec.shown then
      rec.shown = rec.vid == pid and show
      pcall(veh.queueJSUITexture, veh, rec.tag, rec.vid == pid and js or (OSD_JS .. '{"show":false})'))
    end
  end
end

-- ---------------------------------------------------------------- hotkeys (the game's own bindings)

local ACTIONS = { 'toggle', 'prev', 'next', 'stop', 'favorite', 'saveSong', 'compact' }
local capturing = nil   -- the action waiting for a key combination
local bindCache, bindCacheAt = {}, -1

local CAPTURE_KEYS = {}
do
  local function add(enumName, control) CAPTURE_KEYS[#CAPTURE_KEYS + 1] = { enumName, control } end
  for c = string.byte('A'), string.byte('Z') do add('Key_' .. string.char(c), string.char(c):lower()) end
  for d = 0, 9 do add('Key_' .. d, tostring(d)) end
  for f = 1, 12 do add('Key_F' .. f, 'f' .. f) end
  add('Key_LeftArrow', 'left'); add('Key_RightArrow', 'right'); add('Key_UpArrow', 'up'); add('Key_DownArrow', 'down')
end

local function keyboardDevice()
  local b = rawget(_G, 'core_input_bindings')
  if not b or type(b.bindings) ~= 'table' then return nil end
  for _, dev in ipairs(b.bindings) do
    if type(dev.devname) == 'string' and dev.devname:find('^keyboard') then return dev, b end
  end
end

local function boundControls()
  if os.clock() - bindCacheAt < 0.5 then return bindCache end
  bindCacheAt, bindCache = os.clock(), {}
  local dev = keyboardDevice()
  if dev then
    for _, b in ipairs(dev.contents.bindings or {}) do
      local name = type(b.action) == 'string' and b.action:match('^geoRadio_(.+)$')
      if name and not b.unused then
        bindCache[name] = bindCache[name] and (bindCache[name] .. ', ' .. b.control) or b.control
      end
    end
  end
  return bindCache
end

local function rebind(name, control)
  local dev, b = keyboardDevice()
  if not dev then return false end
  local contents = deepcopy(dev.contents)
  local out = {}
  for _, bd in ipairs(contents.bindings or {}) do
    if bd.action ~= 'geoRadio_' .. name and not (control and bd.control == control and bd.action:find('^geoRadio_')) then
      out[#out + 1] = bd
    end
  end
  if control then out[#out + 1] = { control = control, action = 'geoRadio_' .. name } end
  contents.bindings = out
  local ok, err = pcall(b.saveBindingsToDisk, contents)
  if not ok then log('E', 'geoRadio', 'rebind: ' .. tostring(err)) end
  bindCacheAt = -1
  return ok
end

local function captureKeys()
  if im.IsKeyPressed(im.Key_Escape, false) then capturing = nil return end
  local mods = {}
  if im.IsKeyDown(im.Key_LeftCtrl) or im.IsKeyDown(im.Key_RightCtrl) then mods[#mods + 1] = 'ctrl' end
  if im.IsKeyDown(im.Key_LeftAlt) or im.IsKeyDown(im.Key_RightAlt) then mods[#mods + 1] = 'alt' end
  if im.IsKeyDown(im.Key_LeftShift) or im.IsKeyDown(im.Key_RightShift) then mods[#mods + 1] = 'shift' end
  for _, k in ipairs(CAPTURE_KEYS) do
    local enum = im[k[1]]
    if enum and im.IsKeyPressed(enum, false) then
      mods[#mods + 1] = k[2]
      rebind(capturing, table.concat(mods, ' '))
      capturing = nil
      return
    end
  end
end

-- ---------------------------------------------------------------- window helpers

local GOLD, GREY = { 1, 0.78, 0.15 }, { 0.78, 0.78, 0.78 }

local function text(s) im.TextUnformatted(tostring(s or '')) end

local function wrapped(s)
  im.PushTextWrapPos(0)
  im.TextUnformatted(tostring(s or ''))
  im.PopTextWrapPos()
end

local function colored(r, g, b, s)
  im.PushStyleColor2(im.Col_Text, im.ImVec4(r, g, b, 1))
  wrapped(s)
  im.PopStyleColor()
end

local function col(c, a) return im.GetColorU322(im.ImVec4(c[1], c[2], c[3], a or 1)) end

local function bars(sig)
  local n = math.floor((sig or 0) * 5 + 0.5)
  return string.rep('|', n) .. string.rep('.', 5 - n)
end

local function tooltip(s)
  if s and s ~= '' and im.IsItemHovered() then
    im.BeginTooltip()
    text(s)
    im.EndTooltip()
  end
end

-- An invisible button the size of the icon; returns clicked, hovered and its top-left corner.
local function iconButton(id, w, h)
  local p = im.GetCursorScreenPos()
  local clicked = im.InvisibleButton(id, im.ImVec2(w, h))
  return clicked, im.IsItemHovered(), p.x, p.y
end

local function star(id, filled, size)
  local clicked, hov, x, y = iconButton(id, size, size)
  local dl = im.GetWindowDrawList()
  local cx, cy = x + size / 2, y + size / 2 + size * 0.05
  local R, r = size * 0.5, size * 0.21
  local pts = {}
  for i = 0, 9 do
    local a = -math.pi / 2 + i * math.pi / 5
    local rad = i % 2 == 0 and R or r
    pts[i + 1] = im.ImVec2(cx + math.cos(a) * rad, cy + math.sin(a) * rad)
  end
  if filled then
    local c, center = col(GOLD), im.ImVec2(cx, cy)
    for i = 1, 10 do im.ImDrawList_AddTriangleFilled(dl, center, pts[i], pts[i % 10 + 1], c) end
  else
    local c = col(hov and GOLD or GREY, hov and 1 or 0.8)
    for i = 1, 10 do im.ImDrawList_AddLine(dl, pts[i], pts[i % 10 + 1], c, 1.3) end
  end
  return clicked
end

local function bookmark(id, filled, h)
  local w = h * 0.72
  local clicked, hov, x, y = iconButton(id, w, h)
  local dl = im.GetWindowDrawList()
  local x0, x1, y0, y1 = x + 1, x + w - 1, y + 1, y + h - 1
  local xm, yn = (x0 + x1) / 2, y1 - h * 0.3
  local p1, p2, p3, p4, p5 = im.ImVec2(x0, y0), im.ImVec2(x1, y0), im.ImVec2(x1, y1), im.ImVec2(xm, yn), im.ImVec2(x0, y1)
  if filled then
    local c = col(GOLD)
    im.ImDrawList_AddTriangleFilled(dl, p1, p2, p3, c)
    im.ImDrawList_AddTriangleFilled(dl, p1, p3, p4, c)
    im.ImDrawList_AddTriangleFilled(dl, p1, p4, p5, c)
  else
    local c = col(hov and GOLD or GREY, hov and 1 or 0.85)
    local pts = { p1, p2, p3, p4, p5 }
    for i = 1, 5 do im.ImDrawList_AddLine(dl, pts[i], pts[i % 5 + 1], c, 1.4) end
  end
  return clicked
end

local function regionLine(r)
  if not r then return '' end
  local parts = {}
  for _, k in ipairs({ 'city_local', 'state_local', 'country_local' }) do
    local v = r[k] ~= '' and r[k] or r[(k:gsub('_local', ''))]
    if v and v ~= '' and v ~= parts[#parts] then parts[#parts + 1] = v end
  end
  return table.concat(parts, ', ')
end

local function filterText() return filterBuf and ffi.string(filterBuf):lower() or '' end

local function matches(s, f)
  if f == '' then return true end
  return (s.name or ''):lower():find(f, 1, true) or (s.tags or ''):lower():find(f, 1, true)
    or (s.state or ''):lower():find(f, 1, true)
end

local function urlencode(s)
  return (s:gsub('[^%w%-_%.~ ]', function(c) return string.format('%%%02X', string.byte(c)) end):gsub(' ', '+'))
end

-- ---------------------------------------------------------------- window parts

local function setting(key, tip, disabled)
  local p = boolPtrs[key]
  if not p then p = im.BoolPtr(false); boolPtrs[key] = p end
  p[0] = data.settings and data.settings[key] and true or false
  if disabled then im.BeginDisabled() end
  if im.Checkbox(tr(key) .. '##gr_' .. key, p) then send({ type = 'set', key = key, value = p[0] }) end
  if disabled then im.EndDisabled() end
  tooltip(tip)
end

local function drawNotice()
  if not notice then return end
  im.Separator()
  colored(1, 0.8, 0.3, notice.text)
  for i, s in ipairs(notice.suggestions) do
    if im.Button(s.name .. '##sug' .. i) then send({ type = 'play', id = s.id }); notice = nil; return end
  end
  if im.SmallButton(tr('dismiss')) then notice = nil end
end

local function drawSaveSong(p)
  local can = p.title and p.title ~= ''
  if bookmark('##grSaveSong', p.saved, 18) and can and not p.saved then send({ type = 'save_song' }) end
  tooltip(p.saved and tr('songSaved') or tr('saveSong'))
end

-- The player: station, song title, the bookmark under it, signal.
local function drawPlaying(p, compact)
  if not p then text(tr('nothing')) return end
  local status = p.status == 'buffering' and ('  (' .. tr('buffering') .. ')')
    or p.status == 'error' and ('  (' .. tr('error') .. ')') or ''
  local online = p.online and ('  [' .. tr('online') .. ']') or ''
  if star('##grFavNow', p.fav, 16) then send({ type = 'fav', id = p.id }) end
  tooltip(p.fav and tr('favDel') or tr('favAdd'))
  im.SameLine()
  colored(0.4, 1, 0.5, freqLabel(p.freq) .. p.name .. online .. status)
  if p.status == 'error' then tooltip(p.error) end
  if p.title and p.title ~= '' then
    im.PushStyleColor2(im.Col_Text, im.ImVec4(1, 0.85, 0.35, 1))
    im.SetWindowFontScale(compact and 1.0 or 1.15)
    wrapped(p.title)
    im.SetWindowFontScale(1)
    im.PopStyleColor()
  else
    colored(0.6, 0.6, 0.6, tr('noTitle'))
  end
  drawSaveSong(p)
  if not p.online then
    im.SameLine()
    im.ProgressBar(p.signal or 0, im.ImVec2(-1, 0), tr('signal') .. ' ' .. math.floor((p.signal or 0) * 100) .. '%')
  end
end

local function playPause()
  if data and data.playing then send({ type = 'stop' }) else send({ type = 'resume' }) end
end

local function setCompact(on)
  ui.compact = on
  restoreSize = not on
  saveUi()
end

local function drawTransport()
  if im.Button('<<') then send({ type = 'prev' }) end
  tooltip(tr('prevTip'))
  im.SameLine()
  local playing = data and data.playing
  if im.Button((playing and tr('pause') or tr('play')) .. '###grPlay') then playPause() end
  if not playing then tooltip(data and data.last and (tr('playTip') .. data.last) or tr('playNear')) end
  im.SameLine()
  if im.Button('>>') then send({ type = 'next' }) end
  tooltip(tr('nextTip'))
end

local function drawHotkeys()
  local bound = boundControls()
  wrapped(tr('hotkeysTip'))
  for _, name in ipairs(ACTIONS) do
    text(tr('act')[name])
    im.SameLine(210)
    if capturing == name then
      colored(1, 0.8, 0.3, tr('pressKeys'))
    else
      text(bound[name] or tr('unbound'))
      im.SameLine(330)
      if im.SmallButton(tr('change') .. '##grBind' .. name) then capturing = name end
      im.SameLine()
      if im.SmallButton(tr('clear') .. '##grClr' .. name) then rebind(name, nil) end
    end
  end
  if capturing then captureKeys() end
end

local function drawSettings()
  local s = data.settings or {}
  text(tr('soundMode'))
  im.SameLine()
  if im.RadioButton1(tr('realistic') .. '##grReal', s.sound_mode ~= 'practical') then
    send({ type = 'set', key = 'sound_mode', value = 'realistic' })
  end
  tooltip(tr('realisticTip'))
  im.SameLine()
  if im.RadioButton1(tr('practical') .. '##grPrac', s.sound_mode == 'practical') then
    send({ type = 'set', key = 'sound_mode', value = 'practical' })
  end
  tooltip(tr('practicalTip'))
  setting('need_ignition', tr('need_ignitionTip'), s.sound_mode == 'practical')
  setting('mute_unfocused')
  setting('mute_paused')
  setting('follow_master')
  setting('car_screen', tr('car_screenTip'))
  setting('auto_resume')
  setting('autostart', tr('autostartTip'))
  im.SliderFloat(tr('radius'), radiusPtr, 10, 300, '%.0f')
  if im.IsItemDeactivatedAfterEdit() then send({ type = 'radius', km = radiusPtr[0] }) end
  if im.SmallButton(ui.lang == 'ru' and 'English' or 'Русский') then
    ui.lang = ui.lang == 'ru' and 'en' or 'ru'
    saveUi()
    send({ type = 'lang', lang = ui.lang })
  end
  if im.TreeNode1(tr('hotkeys') .. '###grHotkeys') then
    drawHotkeys()
    im.TreePop()
  elseif capturing then
    capturing = nil
  end
end

local function drawStationList(list, online, playingId)
  local f = filterText()
  for _, s in ipairs(list) do
    if matches(s, f) then
      local fav = online or s.fav
      if star('##fav' .. s.id, fav, 15) then send({ type = 'fav', id = s.id }) end
      tooltip(fav and tr('favDel') or tr('favAdd'))
      im.SameLine()
      local label
      if online then
        label = string.format('%s%s%s##%s', freqLabel(s.freq), s.name, s.cc ~= '' and ('  (' .. s.cc .. ')') or '', s.id)
      else
        local where = s.dist and (string.format('%.0f', s.dist) .. tr('km')) or (tr('kind')[s.kind] or s.kind)
        label = string.format('%s  %-8s %s%s##%s', bars(s.signal), where, freqLabel(s.freq), s.name, s.id)
      end
      if im.Selectable1(label, s.id == playingId) then send({ type = 'play', id = s.id, online = online }) end
      local info = {}
      if s.tags and s.tags ~= '' then info[#info + 1] = s.tags end
      if s.state and s.state ~= '' then info[#info + 1] = s.state end
      if s.codec then info[#info + 1] = s.codec .. ((s.bitrate or 0) > 0 and (' ' .. s.bitrate .. ' kbps') or '') end
      tooltip(table.concat(info, '\n'))
    end
  end
end

local function drawSongs(songs, height)
  text(string.format(tr('songs'), #songs))
  im.SameLine()
  if im.SmallButton(tr('openFile') .. '##grSongsFile') then
    local path = '/mods/unpacked/geo_radio/helper/saved_songs.txt'
    pcall(Engine.Platform.exploreFolder, FS:fileExists(path) and path or '/mods/unpacked/geo_radio/helper/')
  end
  im.BeginChild1('##grSongs', im.ImVec2(0, height), true)
  if #songs == 0 then wrapped(tr('noSongs')) end
  for i, s in ipairs(songs) do
    if im.SmallButton(tr('copy') .. '##grCp' .. i) then
      im.SetClipboardText(s.title)
      ui_message(tr('copied') .. s.title, 3, 'geoRadioCopy')
    end
    im.SameLine()
    if im.SmallButton(tr('youtube') .. '##grYt' .. i) then
      openWebBrowser('https://www.youtube.com/results?search_query=' .. urlencode(s.title))
    end
    im.SameLine()
    if im.SmallButton('x##grDel' .. i) then send({ type = 'del_song', i = i - 1 }) end
    tooltip(tr('delete'))
    im.SameLine()
    text(s.title)
    tooltip(table.concat({ s.station or '', s.place or '', s.time or '' }, '\n'))
  end
  im.EndChild()
end

local function draw()
  im = im or ui_imgui
  if not im then return end
  if not openPtr then
    openPtr = im.BoolPtr(true)
    volPtr = im.FloatPtr(0.6)
    radiusPtr = im.FloatPtr(80)
    filterBuf = im.ArrayChar(128)
  end
  openPtr[0] = true
  if restoreSize then
    im.SetNextWindowSize(im.ImVec2(ui.w, ui.h), im.Cond_Always)
    restoreSize = false
  end
  local flags = ui.compact and im.WindowFlags_AlwaysAutoResize or 0
  if im.Begin(tr('title') .. '###geoRadio', openPtr, flags) then
    if not ui.compact then
      local sz = im.GetWindowSize()
      if math.abs(sz.x - ui.w) >= 1 or math.abs(sz.y - ui.h) >= 1 then
        ui.w, ui.h = sz.x, sz.y
        sizeDirty = true
      end
    end
    if connState ~= 'connected' then
      colored(1, 0.5, 0.4, connState == 'connecting' and tr('connecting') or tr('helperOff'))
    elseif noEarth then
      colored(1, 0.8, 0.3, tr('noEarth'))
    end

    if data and connState == 'connected' then
      if not im.IsAnyItemActive() then
        volPtr[0] = data.volume or volPtr[0]
        radiusPtr[0] = data.radius or radiusPtr[0]
      end

      if ui.compact then
        drawPlaying(data.playing, true)
        drawTransport()
        im.SameLine()
        if im.Button(tr('expand')) then setCompact(false) end
        im.PushItemWidth(220)
        if im.SliderFloat('##grVolC', volPtr, 0, 1, '%.2f') then send({ type = 'volume', v = volPtr[0] }) end
        im.PopItemWidth()
        if notice then drawNotice() end
      else
        text(tr('region') .. regionLine(data.region))
        if lastLat then
          text(string.format('%.4f, %.4f', lastLat, lastLon))
          im.SameLine()
        end
        if data.lookup == 'busy' then text(tr('searching'))
        elseif data.lookup and data.lookup:sub(1, 5) == 'error' then colored(1, 0.5, 0.4, data.lookup)
        else text('') end
        im.Separator()
        drawPlaying(data.playing, false)
        if data.focused == false and data.settings and data.settings.mute_unfocused then colored(0.7, 0.7, 0.7, tr('muted')) end
        drawTransport()
        im.SameLine()
        if im.Button(tr('compact')) then setCompact(true) end
        if im.SliderFloat(tr('volume'), volPtr, 0, 1, '%.2f') then send({ type = 'volume', v = volPtr[0] }) end

        drawNotice()
        if im.CollapsingHeader1(tr('settings')) then drawSettings() elseif capturing then capturing = nil end

        local list, favs, songs = data.stations or {}, data.favorites or {}, data.songs or {}
        local playingId = data.playing and data.playing.id
        im.PushItemWidth(-70)
        im.InputTextWithHint('##grFilter', tr('filter'), filterBuf, 128)
        im.PopItemWidth()
        im.SameLine()
        if im.SmallButton(tr('refresh')) then send({ type = 'refresh' }) end

        local songsH = math.max(70, math.min(170, #songs * 24 + 16))
        local listH = math.max(120, im.GetContentRegionAvail().y - songsH - 60)
        if im.BeginTabBar('##grTabs') then
          if im.BeginTabItem(string.format(tr('nearby'), #list) .. '###grNear') then
            im.BeginChild1('##grNearList', im.ImVec2(0, listH), true)
            if #list == 0 then text(tr('none')) end
            drawStationList(list, false, playingId)
            im.EndChild()
            im.EndTabItem()
          end
          if im.BeginTabItem(string.format(tr('favorites'), #favs) .. '###grFavs') then
            im.BeginChild1('##grFavList', im.ImVec2(0, listH), true)
            wrapped(#favs == 0 and tr('noFavs') or tr('favsHint'))
            drawStationList(favs, true, playingId)
            im.EndChild()
            im.EndTabItem()
          end
          im.EndTabBar()
        end
        drawSongs(songs, songsH)
      end
    end
  end
  im.End()
  if not openPtr[0] then ui.visible = false; saveUi() end
end

-- ---------------------------------------------------------------- hooks

local function onUpdate(dtReal)
  local ok, err = pcall(pumpSocket, dtReal)
  if not ok then log('E', 'geoRadio', 'socket: ' .. tostring(err)); closeSock() end

  posTimer = posTimer - dtReal
  if posTimer <= 0 then
    posTimer = POS_EVERY_S
    local lat, lon = playerLatLon()
    noEarth = lat == nil
    if lat then
      lastLat, lastLon = lat, lon
      send({ type = 'pos', lat = lat, lon = lon })
    end
    pcall(hookNav)
  end

  vehTimer = vehTimer - dtReal
  if vehTimer <= 0 then
    vehTimer = VEH_EVERY_S
    pcall(pollVehicle)
  end

  envTimer, envHeartbeat = envTimer - dtReal, envHeartbeat - dtReal
  if envTimer <= 0 and connState == 'connected' then
    envTimer = ENV_EVERY_S
    local okE, env = pcall(currentEnv)
    if okE and env and (envChanged(env, lastEnv) or envHeartbeat <= 0) then
      envHeartbeat = 1
      lastEnv = env
      send(env)
    end
  end

  screenTimer = screenTimer - dtReal
  if screenTimer <= 0 then
    screenTimer = SCREEN_EVERY_S
    local okS, errS = pcall(updateScreens)
    if not okS then log('W', 'geoRadio', 'car screen: ' .. tostring(errS)) end
  end

  if ui.visible and levelReady then
    local okDraw, errDraw = pcall(draw)
    if not okDraw then log('E', 'geoRadio', 'window: ' .. tostring(errDraw)); ui.visible = false end
    if sizeDirty and im and not im.IsMouseDown(0) then sizeDirty = false; saveUi() end
  end
end

local function onClientPostStartMission() levelReady = true end
local function onClientEndMission() levelReady = false end

function M.toggleWindow() ui.visible = not ui.visible; saveUi() end
function M.toggleCompact() setCompact(not ui.compact) end
function M.next() send({ type = 'next' }) end
function M.prev() send({ type = 'prev' }) end
function M.stop() playPause() end
function M.toggleFavorite() if data and data.playing then send({ type = 'fav', id = data.playing.id }) end end
function M.saveSong() send({ type = 'save_song' }) end
function M.status()
  return { conn = connState, lat = lastLat, lon = lastLon, env = lastEnv, ignition = ignition, doors = doorsOpen,
    screens = tableSize(screens) }
end

M.onExtensionLoaded = loadUi
M.onUpdate = onUpdate
M.onClientPostStartMission = onClientPostStartMission
M.onClientEndMission = onClientEndMission
M.onExtensionUnloaded = closeSock

return M
