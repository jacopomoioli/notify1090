# notify1090

Minimal python script (no extra deps) that polls a [tar1090](https://github.com/wiedehopf/tar1090) ADS-B receiver JSON aircraft APIs, filters aircraft within given radius, asks an LLM (Gemini) if that plane is interesting for you using a custom prompt and if it is sends you a Telegram notification. 

![Telegram](https://github.com/user-attachments/assets/84cc7c1f-bfd6-4690-8575-eec757649a17)

## Filtering Flow
1) Fetch all aircrafts from tar1090
2) Filter by radius
3) Skip notification if aircraft already seen
4) Notify directly if aircraft has emergency squawk
5) Skip notification if aircraft type matches the exclusion regex
6) Skip notification if LLM doesn't think the aircraft is interesting
7) Fetch planespotter photo & aircraft route and airlane via adsbdb
8) Send the telegram message

## Integrations
- [tar1090](https://github.com/wiedehopf/tar1090) instance to get aircraft data. I use the one integrated with the excellent [adsb.im](https://adsb.im/home).
- [Google AI Studio](https://aistudio.google.com/) for the interesting aircraft evaluation. If you want this step an API key is needed, but you can skip this step and get notified for every plane in range using the `--notify-all` option.
- [Planespotters](https://www.planespotters.net/), for getting the picture of the plane using the telegram link preview. No API key needed, idk if they implement some kind of rate limiting.
- [ADSBExchange](https://www.adsbexchange.com/) link is returned for each plane, if you want to keep track of it even if it goes out from your ADS/B receiver range.
- [adsbdb](https://www.adsbdb.com/) for getting airline and flight route info from the callsign. No API key needed.

## Usage
Copy the `conf.json.example` to `conf.json` and fill it out with your data. Then, run 

```bash
python3 notify1090.py
```

If you want to skip the LLM evaluation, run 

```bash
python3 notify1090.py --notify-all
```

And if you want to wipe the seen-aircraft database

```bash
python3 notify1090.py --wipe-db
```
(or you just delete the `notify1090.db` sqlite file)

## Config reference

| Field | Description |
|---|---|
| `tar1090_url` | Base URL of your tar1090 instance |
| `latitude` / `longitude` | Your location |
| `radius_km` | Notification radius |
| `prompt` | Natural language description of what you find interesting. Must end with instructions `Reply with YES or NO followed by a colon and a one-line reason.`. |
| `poll_interval_seconds` | How often to poll tar1090 |
| `seen_ttl_hours` | Hours after a previously seen aircraft is re-evaluated. Default: `1` |
| `exclude_type_regex` | Optional regex to check against the aircraft type field, to filter boring models before asking to the LLM |
| `gemini_api_key` | Google AI Studio API key (requires billing enabled) |
| `telegram_bot_token` | Telegram bot token from @BotFather |
| `telegram_chat_id` | Your Telegram chat ID |
