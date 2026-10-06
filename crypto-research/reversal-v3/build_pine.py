from pathlib import Path
import json
ROOT=Path(__file__).resolve().parent;OUT=ROOT/'output';cfg=json.loads((OUT/'selected.json').read_text())
source=r'''//@version=6
// Frozen long/short research candidate. See Long_Short_Report.md for test results.
// Python tests include historical funding; TradingView Strategy Tester does not.
// Commission 0.15% is a cash-cost proxy for 0.10% fees plus 0.05% adverse fills.
// No compiler or broker execution was available during the research.
strategy("Crypto v3: simple long-short reversal", overlay=true,
     initial_capital=10000, currency=currency.USD, pyramiding=0,
     commission_type=strategy.commission.percent, commission_value=0.15,
     slippage=0, margin_long=100, margin_short=100,
     calc_on_every_tick=false, calc_on_order_fills=false,
     process_orders_on_close=false, use_bar_magnifier=true,
     fill_orders_on_standard_ohlc=true)

maType = input.string("SMA", "Moving average", options=["SMA", "EMA"])
signalTF = input.timeframe("1D", "Signal timeframe", options=["60", "240", "1D"])
fastLength = input.int(5, "Fast length", minval=2)
slowRatio = input.int(2, "Slow/fast ratio", minval=2, maxval=5)
slowLength = fastLength * slowRatio
stopMultiple = input.float(3.0, "Fixed ATR stop", minval=0.5, step=0.5)
cooldownBars = input.int(3, "Signal decisions skipped after stop", minval=0, maxval=10)
riskPercent = input.float(0.5, "Target stop risk (% equity)", minval=0.05, maxval=2.0, step=0.05)
capPercent = input.float(95.0, "Maximum notional (% equity)", minval=1.0, maxval=95.0)
startDate = input.time(timestamp("01 Jan 2024 00:00 +0000"), "Start UTC")
endDate = input.time(timestamp("01 Jan 2100 00:00 +0000"), "End UTC")

f_signal() =>
    fast = maType == "EMA" ? ta.ema(close, fastLength) : ta.sma(close, fastLength)
    slow = maType == "EMA" ? ta.ema(close, slowLength) : ta.sma(close, slowLength)
    [fast, slow, ta.atr(14), bar_index, time_close]

// Values affect orders only when the chart close matches a completed signal close.
[fastMA, slowMA, signalATR, signalBars, signalEnd] = request.security(syminfo.tickerid, signalTF, f_signal(), gaps=barmerge.gaps_off, lookahead=barmerge.lookahead_off)
decision = barstate.isconfirmed and time_close == signalEnd
ready = signalBars >= math.max(100, 2 * slowLength) and not na(signalATR) and signalATR > 0
inWindow = time >= startDate and time < endDate
target = fastMA > slowMA ? 1 : fastMA < slowMA ? -1 : 0
current = strategy.position_size > 0 ? 1 : strategy.position_size < 0 ? -1 : 0

var int waitBars = 0
var int inspectedTrades = 0
var int longTicks = na
var int shortTicks = na
if strategy.closedtrades > inspectedTrades
    for trade = inspectedTrades to strategy.closedtrades - 1
        exitID = strategy.closedtrades.exit_id(trade)
        if exitID == "LongStop" or exitID == "ShortStop"
            waitBars := cooldownBars
inspectedTrades := strategy.closedtrades

if decision and ready and inWindow and strategy.equity > 0 and current != target
    if current == 0 and waitBars > 0
        waitBars -= 1
    else if target == 0
        strategy.close_all(alert_message="FLAT")
    else
        ticks = math.max(1, int(math.round(stopMultiple * signalATR / syminfo.mintick)))
        distance = ticks * syminfo.mintick
        riskQty = strategy.equity * riskPercent / 100.0 / (distance * syminfo.pointvalue)
        capQty = strategy.equity * capPercent / 100.0 / (close * syminfo.pointvalue * 1.0015)
        qty = math.floor(math.min(riskQty, capQty) / syminfo.mincontract) * syminfo.mincontract
        if qty > 0
            if target == 1
                longTicks := ticks
                strategy.entry("L", strategy.long, qty=qty, alert_message="LONG_OR_REVERSE_TO_LONG")
            else
                shortTicks := ticks
                strategy.entry("S", strategy.short, qty=qty, alert_message="SHORT_OR_REVERSE_TO_SHORT")

// Stops are attached before the new entry fills and remain fixed at entry distance.
if not na(longTicks)
    strategy.exit("LongStop", from_entry="L", loss=longTicks, alert_message="LONG_STOP")
if not na(shortTicks)
    strategy.exit("ShortStop", from_entry="S", loss=shortTicks, alert_message="SHORT_STOP")
if barstate.isconfirmed and not inWindow and strategy.position_size != 0
    strategy.close_all(alert_message="WINDOW_EXIT")

plot(fastMA, "Signal fast MA", color=color.teal)
plot(slowMA, "Signal slow MA", color=color.orange)
bgcolor(strategy.position_size > 0 ? color.new(color.teal, 92) : strategy.position_size < 0 ? color.new(color.red, 92) : na)
// Use standard perpetual-price charts. Tested execution clocks: 5m,10m,1h,4h.
// Forming MA plots can change between signal closes; orders use confirmed decision closes.
// Any changed inputs invalidate the frozen-default test evidence.
// For .10% fee + tick slippage modelling, change commission first; avoid double charging.
'''
assert cfg['family']=='sma' and cfg['length']==5 and cfg['slow_ratio']==2 and cfg['signal_minutes']==1440 and cfg['stop_atr']==3 and cfg['cooldown']==3
(OUT/'Long_Short_Reversal.pine').write_text(source)
print('Frozen Pine defaults exported')
