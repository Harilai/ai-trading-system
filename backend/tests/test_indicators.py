import pytest
from app.indicators import sma, ema, rsi, macd, bollinger_bands

def test_sma_and_ema():
    values=[1,2,3,4,5]
    assert sma(values,3)==pytest.approx(4)
    assert ema(values,3)>3

def test_rsi_uptrend():
    assert rsi(list(range(1,16)),14)==pytest.approx(100)

def test_macd_shape():
    assert set(macd([float(x) for x in range(1,41)]))=={"macd","signal","histogram"}

def test_bollinger_order():
    bands=bollinger_bands([float(x) for x in range(1,21)])
    assert bands["lower"]<bands["middle"]<bands["upper"]

def test_insufficient_history():
    with pytest.raises(ValueError): sma([1,2],3)
