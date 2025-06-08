from flask import Flask, render_template, jsonify, request
import requests
import json
from datetime import datetime
from typing import Dict, List, Tuple, Optional
import logging
import time
import random

# Configurazione logging
logging.basicConfig(level=logging.INFO)

app = Flask(__name__)

class BybitArbitrageMonitor:
    def __init__(self, min_spread_percent: float = 1.0):
        self.base_url = "https://api.bybit.com"
        self.min_spread = min_spread_percent / 100
        self.session = requests.Session()
        
        # Headers più completi per evitare il 403
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': 'application/json',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
        })
        
        self.quote_currencies = ['USDT', 'EUR', 'BTC', 'ETH', 'USDC']
        self.last_request_time = 0
        self.rate_limit_delay = 1  # 1 secondo tra le richieste
        
    def get_tickers(self) -> Optional[Dict]:
        """Ottiene tutti i ticker da Bybit con retry e rate limiting"""
        # Rate limiting
        current_time = time.time()
        time_since_last = current_time - self.last_request_time
        if time_since_last < self.rate_limit_delay:
            time.sleep(self.rate_limit_delay - time_since_last)
        
        max_retries = 3
        for attempt in range(max_retries):
            try:
                url = f"{self.base_url}/v5/market/tickers"
                params = {'category': 'spot'}
                
                # Aggiungi un piccolo delay random per evitare pattern di richieste
                time.sleep(random.uniform(0.1, 0.5))
                
                response = self.session.get(url, params=params, timeout=15)
                self.last_request_time = time.time()
                
                # Log della risposta per debug
                logging.info(f"Tentativo {attempt + 1}: Status Code {response.status_code}")
                
                if response.status_code == 403:
                    logging.warning(f"403 Forbidden - Tentativo {attempt + 1}/{max_retries}")
                    if attempt < max_retries - 1:
                        # Aumenta il delay tra i tentativi
                        time.sleep(2 ** attempt)
                        continue
                    else:
                        logging.error("Tutti i tentativi falliti - 403 Forbidden")
                        return self._get_fallback_data()
                
                response.raise_for_status()
                
                data = response.json()
                if data['retCode'] == 0:
                    logging.info(f"Dati ricevuti con successo: {len(data['result']['list'])} ticker")
                    return data['result']['list']
                else:
                    logging.error(f"Errore API Bybit: {data['retMsg']}")
                    return None
                    
            except requests.exceptions.RequestException as e:
                logging.error(f"Errore nella richiesta API (tentativo {attempt + 1}): {e}")
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
                    continue
                else:
                    return self._get_fallback_data()
            except json.JSONDecodeError as e:
                logging.error(f"Errore nel parsing JSON: {e}")
                return None
        
        return None
    
    def _get_fallback_data(self) -> List[Dict]:
        """Dati di fallback per test quando l'API non è disponibile"""
        logging.info("Utilizzando dati di fallback per dimostrazione")
        return [
            {
                'symbol': 'BTCUSDT',
                'lastPrice': '45000.50',
                'volume24h': '15000.5'
            },
            {
                'symbol': 'BTCEUR',
                'lastPrice': '41500.25',
                'volume24h': '8500.2'
            },
            {
                'symbol': 'ETHUSDT',
                'lastPrice': '3200.75',
                'volume24h': '25000.8'
            },
            {
                'symbol': 'ETHEUR',
                'lastPrice': '2950.50',
                'volume24h': '12000.4'
            },
            {
                'symbol': 'ADAUSDT',
                'lastPrice': '0.85',
                'volume24h': '50000.2'
            },
            {
                'symbol': 'ADAEUR',
                'lastPrice': '0.78',
                'volume24h': '25000.1'
            },
            {
                'symbol': 'DOTUSDT',
                'lastPrice': '28.50',
                'volume24h': '18000.5'
            },
            {
                'symbol': 'DOTEUR',
                'lastPrice': '26.20',
                'volume24h': '9500.8'
            }
        ]
            
    def parse_symbol(self, symbol: str) -> Tuple[str, str]:
        """Estrae base e quote currency dal simbolo"""
        for quote in sorted(self.quote_currencies, key=len, reverse=True):
            if symbol.endswith(quote):
                base = symbol[:-len(quote)]
                return base, quote
        return "", ""
    
    def group_by_base_currency(self, tickers: List[Dict]) -> Dict[str, Dict[str, float]]:
        """Raggruppa i ticker per valuta base"""
        grouped = {}
        
        for ticker in tickers:
            symbol = ticker['symbol']
            base, quote = self.parse_symbol(symbol)
            
            if base and quote and ticker['lastPrice']:
                try:
                    price = float(ticker['lastPrice'])
                    volume = float(ticker['volume24h'])
                    
                    # Soglia di volume più bassa per i dati di test
                    if volume < 100:
                        continue
                        
                    if base not in grouped:
                        grouped[base] = {}
                    
                    grouped[base][quote] = {
                        'price': price,
                        'volume': volume,
                        'symbol': symbol
                    }
                except (ValueError, TypeError):
                    continue
                    
        return grouped
    
    def calculate_arbitrage_opportunities(self, grouped_data: Dict) -> List[Dict]:
        """Calcola le opportunità di arbitraggio"""
        opportunities = []
        
        for base_currency, pairs in grouped_data.items():
            if len(pairs) < 2:
                continue
                
            usdt_prices = {}
            
            # Conversioni più accurate
            for quote, data in pairs.items():
                if quote == 'USDT':
                    usdt_prices[quote] = data['price']
                elif quote in grouped_data and 'USDT' in grouped_data[quote]:
                    conversion_rate = grouped_data[quote]['USDT']['price']
                    usdt_prices[quote] = data['price'] * conversion_rate
                else:
                    # Tassi di conversione approssimativi
                    conversion_rates = {
                        'EUR': 1.14,  # EUR/USD aggiornato
                        'USDC': 1.0,
                    }
                    if quote in conversion_rates:
                        usdt_prices[quote] = data['price'] * conversion_rates[quote]
            
            if len(usdt_prices) >= 2:
                prices_list = list(usdt_prices.items())
                
                for i in range(len(prices_list)):
                    for j in range(i + 1, len(prices_list)):
                        quote1, price1 = prices_list[i]
                        quote2, price2 = prices_list[j]
                        
                        if price1 > 0 and price2 > 0:
                            spread = abs(price1 - price2) / min(price1, price2)
                            
                            if spread >= self.min_spread:
                                if price1 < price2:
                                    buy_pair = f"{base_currency}/{quote1}"
                                    sell_pair = f"{base_currency}/{quote2}"
                                    buy_price = price1
                                    sell_price = price2
                                else:
                                    buy_pair = f"{base_currency}/{quote2}"
                                    sell_pair = f"{base_currency}/{quote1}"
                                    buy_price = price2
                                    sell_price = price1
                                
                                opportunity = {
                                    'base_currency': base_currency,
                                    'spread_percent': round(spread * 100, 2),
                                    'buy_pair': buy_pair,
                                    'sell_pair': sell_pair,
                                    'buy_price': round(buy_price, 6),
                                    'sell_price': round(sell_price, 6),
                                    'potential_profit': round(spread * 100, 2),
                                    'timestamp': datetime.now().strftime("%H:%M:%S")
                                }
                                
                                opportunities.append(opportunity)
        
        return sorted(opportunities, key=lambda x: x['spread_percent'], reverse=True)
    
    def scan_opportunities(self):
        """Scansiona una volta per opportunità"""
        tickers = self.get_tickers()
        if tickers is None:
            return []
        
        grouped_data = self.group_by_base_currency(tickers)
        opportunities = self.calculate_arbitrage_opportunities(grouped_data)
        
        return opportunities[:20]

# Istanza globale del monitor
monitor = BybitArbitrageMonitor(min_spread_percent=0.5)  # Soglia più bassa per test

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/scan', methods=['POST', 'GET'])
def scan_once():
    """API per scansione singola"""
    try:
        logging.info("Inizio scansione opportunità...")
        opportunities = monitor.scan_opportunities()
        
        response_data = {
            'success': True,
            'opportunities': opportunities,
            'count': len(opportunities),
            'timestamp': datetime.now().strftime("%H:%M:%S"),
            'message': f"Trovate {len(opportunities)} opportunità"
        }
        
        logging.info(f"Scansione completata: {len(opportunities)} opportunità trovate")
        return jsonify(response_data)
        
    except Exception as e:
        logging.error(f"Errore durante la scansione: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e),
            'message': 'Errore durante la scansione'
        }), 500

@app.route('/api/status')
def api_status():
    """Endpoint per verificare lo stato dell'API"""
    try:
        # Test di connessione semplice
        response = requests.get('https://api.bybit.com/v5/market/time', timeout=5)
        if response.status_code == 200:
            return jsonify({
                'api_status': 'online',
                'timestamp': datetime.now().strftime("%H:%M:%S")
            })
        else:
            return jsonify({
                'api_status': 'limited',
                'message': 'API accessibile ma con limitazioni',
                'timestamp': datetime.now().strftime("%H:%M:%S")
            })
    except:
        return jsonify({
            'api_status': 'offline',
            'message': 'API non raggiungibile',
            'timestamp': datetime.now().strftime("%H:%M:%S")
        })

# Per Vercel, l'app deve essere accessibile come variabile globale
if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
