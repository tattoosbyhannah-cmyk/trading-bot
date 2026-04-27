
# Add to master_orchestrator.py imports:
from trading_dashboard import TradingPerformanceDB, save_master_decision_to_db

# Add to run_complete_trading_analysis function before return:
db = TradingPerformanceDB()
save_master_decision_to_db(decision, db)

