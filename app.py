from flask import Flask, render_template, jsonify, request
from flask_socketio import SocketIO, emit
import requests
import json
from datetime import datetime
from typing import Dict, List, Tuple, Optional
import logging
import threading
import time

# Configurazione logging
logging.basicConfig(level=logging.INFO)

app = Flask(__name__)
app.config['SECRET_KEY'] = 'arbitrage_secret_key_2024'
socketio = SocketIO(app, cors_allowed_origins="*")

class BybitArbitrageMonitor:
    def __init__(self, min_spread_percent: float = 1.0):
        self.base_url = "https://api.bybit.com"
        self.min_spread = min_spread_percent / 100
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'ArbitrageBot/1.0'
        })
        
        self.quote_currencies = ['USDT', 'EUR', 'BTC', 'ETH', 'USDC']
        self.alert_cache = {}
        self.cache_duration = 300
        self.auto_monitor_active = False
        self.monitor_thread = None
        
    def get_tickers(self) -> Optional[Dict]:
        """Ottiene tutti i ticker da Bybit"""
        try:
            url = f"{self.base_url}/v5/market/tickers"
            params = {'category': 'spot'}
            
            response = self.session.get(url, params=params, timeout=10)
            response.raise_for_status()
            
            data = response.json()
            if data['retCode'] == 0:
                return data['result']['list']
            else:
                logging.error(f"Errore API Bybit: {data['retMsg']}")
                return None
                
        except requests.exceptions.RequestException as e:
            logging.error(f"Errore nella richiesta API: {e}")
            return None
        except json.JSONDecodeError as e:
            logging.error(f"Errore nel parsing JSON: {e}")
            return None
            
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
                    
                    if volume < 1000:
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
            
            for quote, data in pairs.items():
                if quote == 'USDT':
                    usdt_prices[quote] = data['price']
                elif quote in grouped_data and 'USDT' in grouped_data[quote]:
                    conversion_rate = grouped_data[quote]['USDT']['price']
                    usdt_prices[quote] = data['price'] * conversion_rate
                else:
                    conversion_rates = {
                        'EUR': 1.14,
                        'USDC': 1.0
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
        
        return opportunities[:20]  # Restituisce top 20
    
    def start_auto_monitor(self):
        """Avvia il monitoraggio automatico"""
        self.auto_monitor_active = True
        if self.monitor_thread is None or not self.monitor_thread.is_alive():
            self.monitor_thread = threading.Thread(target=self._auto_monitor_loop)
            self.monitor_thread.daemon = True
            self.monitor_thread.start()
    
    def stop_auto_monitor(self):
        """Ferma il monitoraggio automatico"""
        self.auto_monitor_active = False
    
    def _auto_monitor_loop(self):
        """Loop per il monitoraggio automatico"""
        while self.auto_monitor_active:
            try:
                opportunities = self.scan_opportunities()
                socketio.emit('new_opportunities', {
                    'opportunities': opportunities,
                    'timestamp': datetime.now().strftime("%H:%M:%S"),
                    'count': len(opportunities)
                })
                time.sleep(30)  # 30 secondi
            except Exception as e:
                logging.error(f"Errore nel monitoraggio automatico: {e}")
                time.sleep(30)

# Istanza globale del monitor
monitor = BybitArbitrageMonitor(min_spread_percent=1.0)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/scan', methods=['POST'])
def scan_once():
    """API per scansione singola"""
    try:
        opportunities = monitor.scan_opportunities()
        return jsonify({
            'success': True,
            'opportunities': opportunities,
            'count': len(opportunities),
            'timestamp': datetime.now().strftime("%H:%M:%S")
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/auto-monitor', methods=['POST'])
def toggle_auto_monitor():
    """API per attivare/disattivare monitoraggio automatico"""
    try:
        action = request.json.get('action')
        
        if action == 'start':
            monitor.start_auto_monitor()
            return jsonify({
                'success': True,
                'status': 'started',
                'message': 'Monitoraggio automatico avviato'
            })
        elif action == 'stop':
            monitor.stop_auto_monitor()
            return jsonify({
                'success': True,
                'status': 'stopped',
                'message': 'Monitoraggio automatico fermato'
            })
        else:
            return jsonify({
                'success': False,
                'error': 'Azione non valida'
            }), 400
            
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@socketio.on('connect')
def handle_connect():
    emit('connected', {'message': 'Connesso al server di monitoraggio'})

@socketio.on('disconnect')
def handle_disconnect():
    print('Client disconnesso')

if __name__ == '__main__':
    socketio.run(app, debug=True, host='0.0.0.0', port=5000)