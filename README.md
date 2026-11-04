# Geo Radio

Real radio stations of the place you are driving in [BeamNG.Earth](https://github.com/TecnaGamer/BeamNG.Earth).

Реальные радиостанции того места, где вы едете в [BeamNG.Earth](https://github.com/TecnaGamer/BeamNG.Earth).

[English](#english) · [Русский](#russian)

<a id="english"></a>

## English

Geo Radio looks up the real stations that broadcast where your car is and plays them. Move to another city and the list changes with you. Drive out of range and the station fades into static.

The game itself cannot stream internet audio, so a small Python helper does the lookup and the playback. The in-game window only sends it your position and draws the station list.

### Requirements

- BeamNG.drive
- [BeamNG.Earth](https://github.com/TecnaGamer/BeamNG.Earth). Without it the mod has no coordinates and stays quiet.
- Windows, Python 3.10 or newer, and an internet connection

### Install

1. Copy this folder to the game's user folder, so that the path ends in `mods\unpacked\geo_radio`:

   `%LOCALAPPDATA%\BeamNG\BeamNG.drive\current\mods\unpacked\geo_radio`

   The user folder is the one shown in the BeamNG launcher if you changed it.

2. Start the helper, once:

   - `helper\enable_autostart.bat` starts it hidden and adds it to Windows startup, or
   - `helper\start_georadio.bat` runs it in a window for this session only.

   The first run installs `numpy`, `scipy`, `sounddevice` and `av` if they are missing.

3. Start BeamNG.drive and load a BeamNG.Earth map. The Geo Radio window appears once you are on the map. It stays hidden in the main menu and on the loading screen, and the radio is silent there too.

`helper\disable_autostart.bat` removes the helper from Windows startup.

### Playing

The list shows stations near you, with a signal bar, the distance or the kind (`near`, `region`, `country`) and the frequency when the station's name contains one, such as `91.5 FM` or `106,2`.

- Click a station to play it. `<<` and `>>` tune down and up by frequency, with a burst of static between stations. Stations without a frequency come after the others, in name order.
- **Pause** stops the radio and remembers that. **Play** starts the last station again. After you restart the game, the radio comes back only if it was playing.
- The star adds a station to **Favorites**. A favorite plays anywhere in the world, with no static and no fading.
- The song title is shown on the player. The bookmark under it saves the artist and title. The saved list sits under the station list: copy a title, open a YouTube search, or delete it. The same list is written to `helper\saved_songs.txt`.
- **Compact** shrinks the window to the player. **Expand** restores the size the full window had, including after you resized it.

Leave a station's range and it fades out. Under a weak signal the window suggests stronger local stations. After the signal is gone for a while the station stops. Teleporting drops the previous place's list; if the radio was on, the strongest station of the new place starts.

### Settings

| Setting | What it does |
|---|---|
| Realistic | Loud in the cabin. Outside it is muffled, quieter with distance, and comes from the side the car is on. An open door, tailgate or trunk lets more of it out. |
| Practical | The same volume wherever the camera is. |
| Ignition | In realistic mode the radio needs the ignition on. |
| Mute when unfocused | Silent while the game is minimized or another window is in front. |
| Mute while paused | Silent while the game is paused. |
| Master volume | Follows the game's master volume. |
| Car screen | Station and song on the navigation screen of cars that have one. |
| Resume on start | Plays the last station when a map loads, unless you had paused it. |
| Autostart | The helper starts hidden with Windows. |
| Search radius | How far local stations are looked up, 10 to 300 km. |
| Language | English or Russian. The choice is remembered. English is the default. |
| Hotkeys | Click **Change**, press a combination, Esc cancels. The same bindings are in Options > Controls. |

### Default hotkeys

| Keys | Action |
|---|---|
| Ctrl+Alt+R | Show or hide the window |
| Ctrl+Alt+N | Tune up |
| Ctrl+Alt+B | Tune down |
| Ctrl+Alt+F | Add or remove the playing station from favorites |
| Ctrl+Alt+S | Save the playing song |

Play / pause and compact mode have no keys until you set them.

### How it works

The Lua extension reads the car's latitude and longitude from BeamNG.Earth and sends them, together with the camera position, to the helper on `127.0.0.1:47620`.

The helper asks [Radio Browser](https://www.radio-browser.info/) for stations around that point and in the same city or region, and [Nominatim](https://nominatim.openstreetmap.org/) for the place name. It decodes the stream and plays it through the Windows audio device. Weak stations are mixed with static. Playback never goes through the game's own audio engine, so the "3D" sound outside the car is stereo panning.

### Limits

- The car-screen overlay appears on vehicles that register a navigation screen. A car spawned before the mod loaded may need to be respawned.
- Open doors are detected from the vehicle's coupler signals, which some models do not have.
- Many stations publish no frequency, so tuning falls back to alphabetical order.
- Only one helper can run. A second one reports that port 47620 is busy.

### Privacy

Station search and the place name are requested from Radio Browser and OpenStreetMap Nominatim while you drive. Nothing else is sent. Favorites, saved songs, volume and the last station stay in `helper\config.json` on your machine. That file is not part of this repository.

### License

[MIT](LICENSE).

<a id="russian"></a>

## Русский

Гео-Радио находит настоящие станции, которые вещают там, где стоит ваша машина, и включает их. Переехали в другой город — список сменился. Выехали из зоны — станция уходит в шипение.

Сама игра не умеет воспроизводить интернет-аудио, поэтому поиском и звуком занимается небольшой помощник на Python. Окно в игре только передаёт ему координаты и рисует список станций.

### Что нужно

- BeamNG.drive
- [BeamNG.Earth](https://github.com/TecnaGamer/BeamNG.Earth). Без него у мода нет координат, и радио молчит.
- Windows, Python 3.10 или новее и интернет

### Установка

1. Скопируйте эту папку в пользовательскую папку игры, чтобы путь заканчивался на `mods\unpacked\geo_radio`:

   `%LOCALAPPDATA%\BeamNG\BeamNG.drive\current\mods\unpacked\geo_radio`

   Если папку меняли, берите ту, что указана в лаунчере BeamNG.

2. Один раз запустите помощник:

   - `helper\enable_autostart.bat` запускает его скрыто и добавляет в автозагрузку Windows, либо
   - `helper\start_georadio.bat` запускает его в окне только на этот сеанс.

   При первом запуске сами установятся `numpy`, `scipy`, `sounddevice` и `av`, если их ещё нет.

3. Запустите BeamNG.drive и загрузите карту BeamNG.Earth. Окно Гео-Радио появляется, когда вы уже на карте. В главном меню и на экране загрузки его нет, и радио там тоже молчит.

`helper\disable_autostart.bat` убирает помощник из автозагрузки Windows.

### Как слушать

В списке станции рядом с вами: полоски сигнала, расстояние или тип (`рядом`, `регион`, `страна`) и частота, если она есть в названии, например `91.5 FM` или `106,2`.

- Нажмите станцию, чтобы включить её. `<<` и `>>` перестраивают вниз и вверх по частоте, между станциями короткое шипение. Станции без частоты идут после остальных, по алфавиту.
- **Пауза** выключает радио и запоминает это. **Играть** снова включает последнюю станцию. После перезапуска игры радио включается само только если перед этим играло.
- Звезда добавляет станцию в **Избранное**. Избранное играет в любой точке мира, без помех и без затухания.
- Название песни пишется на плеере. Закладка под ним сохраняет исполнителя и название. Список лежит под списком станций: скопировать, открыть поиск на YouTube или удалить. Тот же список записывается в `helper\saved_songs.txt`.
- **Свернуть** оставляет только плеер. **Развернуть** возвращает окну тот размер, который был у полного окна, в том числе если вы меняли его мышью.

Выехали из зоны станции — она затихает. При слабом сигнале окно предлагает более сильные местные станции. Когда сигнала нет уже какое-то время, станция выключается. Телепорт сбрасывает список прошлого места. Если радио было включено, начинает играть самая сильная станция нового места.

### Настройки

| Настройка | Что делает |
|---|---|
| Реалистичный | Громко в салоне. Снаружи приглушённо, тише с расстоянием и с той стороны, где стоит машина. Открытая дверь, багажник или задний борт выпускают звук сильнее. |
| Практичный | Одна и та же громкость, где бы ни была камера. |
| Зажигание | В реалистичном режиме радио работает только при включённом зажигании. |
| Свёрнутая игра | Тишина, когда игра свёрнута или на переднем плане другое окно. |
| Пауза | Тишина, пока игра на паузе. |
| Общая громкость | Следует за общей громкостью игры. |
| Экран машины | Станция и песня на экране навигации у машин, где он есть. |
| При запуске | Включает последнюю станцию при загрузке карты, если вы не ставили радио на паузу. |
| Автозапуск | Помощник стартует вместе с Windows скрыто. |
| Радиус поиска | Как далеко искать местные станции, от 10 до 300 км. |
| Язык | English или Русский. Выбор запоминается. По умолчанию английский. |
| Горячие клавиши | Нажмите **Изменить**, затем сочетание. Esc отменяет. Те же привязки есть в Настройки > Управление. |

### Клавиши по умолчанию

| Клавиши | Действие |
|---|---|
| Ctrl+Alt+R | Показать или скрыть окно |
| Ctrl+Alt+N | Станция вверх |
| Ctrl+Alt+B | Станция вниз |
| Ctrl+Alt+F | Добавить или убрать текущую станцию из избранного |
| Ctrl+Alt+S | Сохранить играющую песню |

У «Играть / пауза» и компактного режима клавиш нет, пока вы их не назначите.

### Как это устроено

Расширение на Lua берёт широту и долготу машины из BeamNG.Earth и отправляет их вместе с положением камеры помощнику на `127.0.0.1:47620`.

Помощник спрашивает станции вокруг точки и в том же городе или регионе у [Radio Browser](https://www.radio-browser.info/), а название места у [Nominatim](https://nominatim.openstreetmap.org/). Поток он декодирует и играет через звуковое устройство Windows. Слабые станции смешиваются с шипением. Звук не идёт через звуковой движок игры, поэтому «объём» снаружи машины — это стереопанорама.

### Ограничения

- Плашка на экране машины появляется у тех машин, которые регистрируют экран навигации. Машину, заспавненную до загрузки мода, может понадобиться заспавнить заново.
- Открытые двери определяются по сигналам машины, и у части моделей их нет.
- У многих станций частота нигде не указана, и настройка идёт по алфавиту.
- Помощник может быть запущен только один. Второй сообщит, что порт 47620 занят.

### Приватность

Пока вы едете, запрос станций и названия места уходит в Radio Browser и Nominatim (OpenStreetMap). Больше ничего не отправляется. Избранное, сохранённые песни, громкость и последняя станция лежат в `helper\config.json` на вашем компьютере. Этого файла в репозитории нет.

### Лицензия

[MIT](LICENSE).
