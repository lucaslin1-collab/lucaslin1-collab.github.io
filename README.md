# Lucas Lin Markets

The front page at `https://lucaslin1-collab.github.io`, linking each Kalshi bot:

- **Forecast bot:** [`lucaslinweather`](https://github.com/lucaslin1-collab/lucaslinweather).
  Morning picks for daily-high temperature markets.
- **Live bot:** [`weather-live`](https://github.com/lucaslin1-collab/weather-live).
  Intraday paper trading on the same markets.

Each bot card reads that bot's latest `picks.json` or `status.json`, so the page always
shows today's numbers. To add a new bot, add a card to `index.html` and push. The
`Publish hub` workflow republishes the page.
