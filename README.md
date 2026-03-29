# 🚀 AurumPulse: Real-Time Market Correlation Engine

![Python](https://img.shields.io/badge/Python-3.x-blue?style=for-the-badge&logo=python)
![Flask](https://img.shields.io/badge/Flask-API-lightgrey?style=for-the-badge&logo=flask)
![SQLite](https://img.shields.io/badge/SQLite-Database-003B57?style=for-the-badge&logo=sqlite)
![JavaScript](https://img.shields.io/badge/JavaScript-Frontend-F7DF1E?style=for-the-badge&logo=javascript)

AurumPulse is a lightweight, real-time financial dashboard designed to track Gold (XAU) prices and analyze its correlation with the US Dollar Index (DXY). It features a custom algorithmic RSI calculator, automated background data collection, and a dynamic candlestick charting interface.

## 🌟 Key Features

* **Real-Time Data Pipeline:** Fetches 1-minute interval market data using the `yfinance` API.
* **Custom Algorithmic Analysis:** Implements a dependency-free, pure Pandas-based Relative Strength Index (RSI) calculator for technical analysis.
* **Market Correlation Logic:** Analyzes the inverse relationship between Gold and the US Dollar Index to provide contextual market warnings.
* **Automated Background Worker:** Utilizes Python `threading` to continuously fetch and store market data every 60 seconds without blocking the main API thread.
* **Persistent Memory:** Integrates a SQLite database to maintain a historical record of price actions and indicator values.
* **Dynamic UI:** Features a dark-mode, responsive dashboard with live Candlestick charts powered by `ApexCharts`.

## 🛠️ Architecture

* **Backend:** Python, Flask, Pandas, yfinance, Threading
* **Frontend:** HTML5, CSS3, Vanilla JavaScript, ApexCharts
* **Database:** SQLite3

## ⚙️ Local Setup & Installation

1. **Clone the repository:**
   ```bash
   git clone [https://github.com/yourusername/AurumPulse.git](https://github.com/yourusername/AurumPulse.git)
   cd AurumPulse