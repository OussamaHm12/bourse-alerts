# Moroccan Stock Telegram Alerts

GitHub Actions project that checks selected Casablanca Stock Exchange prices every 3 hours and sends Telegram alerts when a configured threshold is reached.

This is a notification-only tool. It does not implement trading, order placement, portfolio management, or investment advice.

## Monitored Stocks

| Stock | Ticker | Alert condition |
| --- | --- | --- |
| TGCC | TGC | price <= 700 MAD |
| Akdital | AKT | price <= 1100 MAD |
| Attijariwafa Bank | ATW | price <= 650 MAD |

The script tries Casablanca Bourse first, then falls back to BMCE Capital Bourse when a source is unavailable or cannot be parsed. Moroccan market data from public sources may be delayed, unavailable outside market hours, or temporarily inconsistent across providers.

## Files

- `stock_alert.py` - price fetcher, threshold checker, Telegram sender, and local alert state handling.
- `requirements.txt` - Python dependencies.
- `.github/workflows/stock-alert.yml` - scheduled and manual GitHub Actions workflow.
- `.env.example` - environment variable template.
- `README.md` - setup and usage instructions.

## Telegram Setup

1. Create a Telegram bot:
   - Open Telegram and message `@BotFather`.
   - Run `/newbot`.
   - Follow the prompts.
   - Copy the bot token.

2. Get your chat ID:
   - Send any message to your new bot.
   - Open this URL in a browser, replacing `<TOKEN>`:

```text
https://api.telegram.org/bot<TOKEN>/getUpdates
```

   - Find the `chat.id` value in the JSON response.
   - For groups, add the bot to the group, send a group message, then call `getUpdates`.

3. Add GitHub Secrets:
   - In your GitHub repository, go to `Settings` -> `Secrets and variables` -> `Actions`.
   - Add `TELEGRAM_BOT_TOKEN`.
   - Add `TELEGRAM_CHAT_ID`.

4. Enable GitHub Actions:
   - Push this repository to GitHub.
   - Open the `Actions` tab.
   - Enable workflows if GitHub asks for confirmation.

5. Test manually:
   - Open `Actions` -> `Moroccan Stock Alert`.
   - Click `Run workflow`.
   - Check the logs and your Telegram chat.

## Local Run

Install dependencies:

```bash
python -m pip install -r requirements.txt
```

Set environment variables:

```bash
export TELEGRAM_BOT_TOKEN="123456789:your-token"
export TELEGRAM_CHAT_ID="123456789"
```

Run:

```bash
python stock_alert.py
```

On Windows PowerShell:

```powershell
$env:TELEGRAM_BOT_TOKEN="123456789:your-token"
$env:TELEGRAM_CHAT_ID="123456789"
python stock_alert.py
```

If Telegram variables are missing, the script still fetches and compares prices, but it logs a warning instead of sending alerts.

## Alert State

The script writes `alert_state.json` to avoid duplicate notifications. It sends one alert when a stock first moves below or equal to its threshold. It resets that stock's alert state after the price rises above the threshold.

GitHub Actions restores and saves this file with the Actions cache, and also uploads it as an artifact for inspection.

## Configuration

Edit the `STOCKS` dictionary in `stock_alert.py` to add or change stocks:

```python
"TGCC": {
    "symbol": "TGC",
    "name": "TGCC",
    "threshold": 700.0,
    "sources": [
        {
            "name": "Casablanca Bourse",
            "url": "https://www.casablanca-bourse.com/live-market/instruments/TGC?pwa=1",
            "parser": "casablanca_bourse",
        },
        {
            "name": "BMCE Capital Bourse",
            "url": "https://www.bmcecapitalbourse.com/bkbbourse/details/115038557%2C102%2C608",
            "parser": "bmce_capital_bourse",
        },
    ],
}
```

Add a new entry with a symbol, name, threshold, and source list. Reuse an existing parser when the page format matches one of the supported sources.

## Telegram Message Format

Alerts look like this:

```text
🚨 Moroccan Stock Alert
Stock: TGCC (TGC)
Current price: 695 MAD
Threshold: 700 MAD
Source: BMCE Capital Bourse
Time: 2026-06-16 14:30:00 UTC
```

## Schedule

The workflow runs every 3 hours:

```yaml
cron: "0 */3 * * *"
```

GitHub schedules use UTC and may start a few minutes later during busy periods.
