# notify1090

Minimal python script (no extra deps) that polls a [tar1090](https://github.com/wiedehopf/tar1090) ADS-B receiver JSON aircraft APIs, filters aircraft within given radius, asks an LLM (Gemini) if that plane is interesting for you using a custom prompt and if it is sends you a Telegram notification. 

![Telegram](https://github.com/user-attachments/assets/d22bf59f-35d9-47f1-8fff-0dca9f3a88fb)


## Integrations
- [tar1090](https://github.com/wiedehopf/tar1090) instance to get aircraft data. I use the one integrated with the excellent [adsb.im](https://adsb.im/home).
- [Google AI Studio](https://aistudio.google.com/) for the interesting aircraft evaluation. If you want this step an API key is needed, but you can skip this step and get notified for every plane in range using the `--notify-all` option.
- [Planespotters](https://www.planespotters.net/), for getting the picture of the plane using the telegram link preview. No API key needed, idk if they implement some kind of rate limiting.
- [ADSBExchange](https://www.adsbexchange.com/) link is returned for each plane, if you want to keep track of it even if it goes out from your ADS/B receiver range.

## Usage
Copy the `conf.json.example` and fill it out with your data. Then, run 

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
