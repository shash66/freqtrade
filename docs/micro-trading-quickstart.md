# Micro-trading quickstart (small balance, ~$5–$10)

**Read first:** automated trading can lose money, and the bundled sample strategy is a
*demo*, not a profitable system. Treat any real money as money you can afford to lose.
Always run **dry-run** (paper trading) for at least 1–2 weeks before going live.

## Reality check on "$5"

- Binance spot requires a minimum order of about **5 USDT**, and fees come out of that.
  A $5 stake is often rejected. This guide uses a **$6 stake** and suggests **$10–$20** total.
- Fees (~0.1% per trade, 0.2% round trip) matter more on tiny balances. Keep trades few.
- Run the bot on **your own computer or a small server**, not in a throwaway cloud session.

## Step 1: Install (on your machine)

```bash
git clone <your fork of this repo> && cd freqtrade
./setup.sh -i          # Linux/macOS (Windows: setup.ps1)
source .venv/bin/activate
```

Or with Docker: see `docs/docker_quickstart.md`.

## Step 2: Create your config and strategy

```bash
freqtrade create-userdir --userdir user_data
cp config_examples/config_micro.example.json config.json
freqtrade new-strategy --strategy MicroStrategy --template sample
```

`config.json` is git-ignored, so your keys stay private.

## Step 3: Paper trade first (no real money)

`config.json` ships with `"dry_run": true` and a fake $10 wallet. Run:

```bash
freqtrade trade --config config.json --strategy MicroStrategy
```

Check results any time:

```bash
freqtrade show-trades --config config.json
```

Optional: backtest on history first:

```bash
freqtrade download-data --config config.json --timerange 20260101- -t 5m
freqtrade backtesting --config config.json --strategy MicroStrategy --timerange 20260101-
```

## Step 4: Go live with a small amount

1. Create an exchange account, deposit ~$10–$20 **USDT** into **spot**.
2. Create an API key with **"Enable Spot Trading" only**. **Never enable withdrawals.**
   Restrict it to your IP if possible.
3. Edit `config.json`:
   - `"dry_run": false`
   - fill `exchange.key` and `exchange.secret`
   - keep `"max_open_trades": 1` and `"stake_amount": 6` (above the exchange minimum)
   - remove `dry_run_wallet` (not used live)
4. Start it: `freqtrade trade --config config.json --strategy MicroStrategy`
5. Watch it: enable Telegram (`telegram` section) or the web UI (`api_server`, after
   changing the password and `jwt_secret_key`).
6. Stop any time with Ctrl+C, or `/stop` and `/forceexit all` in Telegram.

## Safety tips

- Add a stoploss in your strategy (the sample has one) and don't remove it.
- Never share `config.json`. Never commit keys.
- Increase size only after weeks of results you understand.
- Other exchanges (e.g. Kraken) may have lower minimums; change `exchange.name` and pairs
  (see `config_examples/config_kraken.example.json`).
