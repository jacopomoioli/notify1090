# notify1090

Minimal python script that polls a [tar1090](https://github.com/wiedehopf/tar1090) ADS-B receiver JSON aircraft APIs, filters aircraft within given radius, asks an LLM (Gemini) if that plane is interesting for you using a custom prompt and if it is sends you a notification.

| Telegram Notification | NTFY Notification |
|-|-|
|![Telegram](https://i.imgur.com/j1xEXsq.png)|![ntfy](https://i.imgur.com/gRvJjQ1.png)|



## Filtering Flow
1) Fetch all aircrafts from tar1090
2) Filter by radius
3) Skip notification if aircraft already seen
4) Notify directly if aircraft has emergency squawk
5) Skip notification if aircraft type matches the exclusion regex 
6) Skip notification if LLM doesn't think the aircraft is interesting 
7) Fetch planespotter photo and aircraft route via adsbdb
8) Take a tar1090 map screenshot (if playwright and pillow are installed)
9) Compose planespotter photo and screenshot into a single image
10) Send the telegram/ntfy message

## Integrations
- [Telegram](https://telegram.org/) for notifications. Create a bot via [@BotFather](https://t.me/BotFather) and get your chat ID via [@userinfobot](https://t.me/userinfobot). Optional if ntfy is configured.
- [ntfy](https://ntfy.sh/) for notifications. Optional if Telegram is configured.
- [tar1090](https://github.com/wiedehopf/tar1090) instance to get aircraft data. I use the one integrated with the excellent [adsb.im](https://adsb.im/home).
- [Google AI Studio](https://aistudio.google.com/) for the interesting aircraft evaluation. If you want this step an API key is needed, but you can skip this step and get notified for every plane in range using the `--notify-all` option.
- [Planespotters](https://www.planespotters.net/), for getting the picture of the plane. No API key needed.
- [ADSBExchange](https://www.adsbexchange.com/) link is returned for each plane, if you want to keep track of it even if it goes out from your ADS-B receiver range.
- [adsbdb](https://www.adsbdb.com/) for getting airline and flight route info from the callsign. No API key needed.
- [Playwright](https://playwright.dev/python/) for taking a tar1090 map screenshot of the aircraft position. Install with `pip install playwright && playwright install chromium`.
- [Pillow](https://python-pillow.org/) for composing the planespotter photo and map screenshot into a single image. Install with `pip install pillow`.

## Usage
Copy the `conf.json.example` to `conf.json` and fill it out with your data. Then, run

```bash
python3 notify1090.py
```

If you want to skip the LLM evaluation but still apply the type exclusion regex, run

```bash
python3 notify1090.py --skip-llm
```

If you want to skip both the LLM and the regex filter and get notified for every aircraft in radius, run

```bash
python3 notify1090.py --notify-all
```

And if you want to wipe the seen-aircraft database

```bash
python3 notify1090.py --wipe-db
```
(or you just delete the `notify1090.db` sqlite file)


## Logs
All output goes to stdout and `log.txt` in the working directory, and each line has the timestamp.

- `poll #N`: summary of each poll cycle (total / in radius / new)
- `NOTIFY`: aircraft flagged and notification sent, followed by the LLM reason
- `SKIP`: aircraft evaluated but not interesting, followed by the LLM reason
- `EXCLUDE`: aircraft type matched `exclude_type_regex`, skipped before LLM
- `EMERGENCY`: squawk 7500/7600/7700 detected, notified immediately bypassing all filters
- `GEMINI ERROR`: LLM call failed, retrying
- `GEMINI RETRY FAILED`: LLM timed out twice, aircraft notified anyway
- `TELEGRAM ERROR`: Telegram notification failed to send
- `NTFY ERROR`: ntfy notification failed to send
- `PLANESPOTTERS ERROR`: photo lookup failed
- `ADSBDB ERROR`: route lookup failed
- `SCREENSHOT ERROR`: tar1090 screenshot failed
- `COMPOSE ERROR`: image composition failed

Analyzing the LLM message inside the NOTIFY and SKIP logs could be useful for verifying if the LLM evaluation matches your expectation. You can get all these log lines with

```bash
grep "LLM REASON" log.txt
```

By executing the following file
```python
python3 web.py --port 8080
```
it's possible to start a small web server that reads the log.txt file and prints every aircraft decision entry (NOTIFY, SKIP, EXCLUDE, EMERGENCY) in a easier way.

| Log Viewer |
|-|
|![Log Viewer](https://i.imgur.com/pvtlIC3.png)|

## Config reference

| Field | Description |
|---|---|
| `tar1090_url` | Base URL of your tar1090 instance |
| `latitude` / `longitude` | Your location |
| `radius_km` | Notification radius |
| `prompt` | Natural language description of what you find interesting. Must end with instructions `Reply with YES or NO followed by a colon and a one-line reason.`. |
| `poll_interval_seconds` | How often to poll tar1090 |
| `seen_ttl_hours` | Hours after a previously seen aircraft is re-evaluated.|
| `exclude_type_regex` | Optional regex to check against the aircraft type field, to filter boring models before asking to the LLM |
| `gemini_api_key` | Google AI Studio API key (requires billing enabled) |
| `telegram_bot_token` | Telegram bot token from @BotFather. Optional if `ntfy_topic` is set. |
| `telegram_chat_id` | Your Telegram chat ID. Required if `telegram_bot_token` is set. |
| `ntfy_topic` | ntfy topic name. Optional if Telegram is configured. |
| `screenshot` | Enable tar1090 position screenshot. |
| `screenshot_params` | Full tar1090 query string for the screenshot URL. See [tar1090 query docs](https://github.com/wiedehopf/tar1090/blob/master/README-query.md). |
| `screenshot_viewport` | Viewport width in px |
