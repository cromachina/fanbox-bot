import main
import logging

logging.basicConfig(
    format='[%(asctime)s][%(levelname)s] %(message)s',
    level=logging.DEBUG,
)

def filter_future_dates(txns, current_date):
    return [txn for txn in txns if main.parse_date(txn['transactionDatetime']) <= current_date]

test_plan_fee_lookup = {
    500: '1',
    1000: '2',
}

test_txns = [
    {
        'paidAmount': 500,
        'transactionDatetime': '2024-05-14T00:00:00+09:00',
        'targetMonth': '2024-05',
    },
    {
        'paidAmount': 500,
        'transactionDatetime': '2024-04-01T00:00:00+09:00',
        'targetMonth': '2024-04',
    },
    {
        'paidAmount': 500,
        'transactionDatetime': '2024-03-15T00:00:00+09:00',
        'targetMonth': '2024-03',
    },
]

current_date = main.parse_date('2024-06-15T00:00:01+09:00')

test_txns = filter_future_dates(test_txns, current_date)

print(main.compute_plan_id(test_txns, test_plan_fee_lookup, current_date, 5, True))