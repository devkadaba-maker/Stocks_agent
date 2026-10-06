from pathlib import Path
import json
import engine as e
ROOT=e.ROOT;OUT=ROOT/'output'
c=json.loads((OUT/'selected.json').read_text())
assert c['family']=='ema','Exporter implements this experiment\'s frozen EMA winner'
assert e.hash_file(OUT/'selected.json')==(OUT/'selected.sha256').read_text()
source='''//@version=6
// Optimized research candidate. Read Crypto_Strategy_Report.html for fresh-data tests.
// Spot long-only; fixed 0.5% target risk. No performance or profit guarantee.
// Primary Python costs: 0.10% fee + 0.05% adverse price slippage PER SIDE.
// Pine default 0.15% commission is an all-in CASH-COST PROXY, with 0 extra ticks.
// To use tick slippage instead, first change commission to 0.10% in Properties.
// Pine was reviewed against v6 docs; no TradingView compiler/forward test was available.
strategy("Crypto Research v2: confirmed EMA trend", overlay=true,
     initial_capital=10000, currency=currency.USD, pyramiding=0,
     commission_type=strategy.commission.percent, commission_value=0.15,
     slippage=0, margin_long=100, margin_short=100,
     calc_on_every_tick=false, calc_on_order_fills=false,
     process_orders_on_close=false, use_bar_magnifier=false,
     fill_orders_on_standard_ohlc=true)

signalTF = input.timeframe("SIGNALTF", "Frozen signal timeframe", options=["240", "1D"])
fastLength = input.int(FAST, "Fast EMA length on signal timeframe", minval=2)
slowLength = fastLength * 5
stopMultiple = input.float(STOP, "ATR stop multiple", minval=0.5, step=0.25)
useTrailing = input.bool(TRAIL, "Trail stop at completed chart closes")
adxThreshold = input.int(ADX, "Entry ADX minimum (0 disables)", minval=0, maxval=50)
cooldownLength = input.int(COOL, "Decision closes skipped after exit", minval=0, maxval=20)
riskPercent = input.float(0.5, "Initial stop risk target (% equity)", minval=0.05, maxval=2, step=0.05)
positionCap = input.float(95.0, "Spot notional cap (% equity)", minval=1, maxval=95)
startDate = input.time(timestamp("01 Jan 2024 00:00 +0000"), "Test start UTC")
endDate = input.time(timestamp("01 Jan 2100 00:00 +0000"), "Test end UTC")

// This function is evaluated in the SIGNAL context.
f_confirmed_inputs() =>
    [plusDI, minusDI, adx] = ta.dmi(14, 14)
    [ta.ema(close, fastLength), ta.ema(close, slowLength), ta.atr(14), adx, bar_index, time_close]

// Current signal values may affect orders ONLY on the chart candle whose close
// exactly matches the signal candle's close. No intermediate HTF signal is traded.
[signalFast, signalSlow, signalATR, signalADX, signalBars, signalEnd] = request.security(syminfo.tickerid, signalTF, f_confirmed_inputs(), gaps=barmerge.gaps_off, lookahead=barmerge.lookahead_off)
previousATR = request.security(syminfo.tickerid, signalTF, ta.atr(14)[1], gaps=barmerge.gaps_off, lookahead=barmerge.lookahead_on)
decisionClose = barstate.isconfirmed and time_close == signalEnd
confirmedATR = decisionClose ? signalATR : previousATR
warmup = math.max(100, slowLength)
ready = barstate.isconfirmed and signalBars >= warmup and not na(confirmedATR) and confirmedATR > 0
inWindow = time >= startDate and time < endDate
enterSignal = decisionClose and signalFast > signalSlow and (adxThreshold == 0 or signalADX > adxThreshold)
exitSignal = decisionClose and signalFast <= signalSlow

var float initialDistance = na
var float protectiveStop = na
var int cooldown = 0
var int closedCount = 0
newExit = strategy.closedtrades > closedCount
if newExit
    cooldown := cooldownLength
closedCount := strategy.closedtrades

if strategy.position_size == 0
    protectiveStop := na
    initialDistance := na
    if ready and inWindow and decisionClose
        if cooldown > 0
            if not newExit
                cooldown -= 1
        else if enterSignal and strategy.equity > 0
            stopTicks = math.max(1, int(math.round(stopMultiple * confirmedATR / syminfo.mintick)))
            initialDistance := stopTicks * syminfo.mintick
            riskCash = strategy.equity * riskPercent / 100.0
            riskQty = riskCash / (initialDistance * syminfo.pointvalue)
            capQty = strategy.equity * positionCap / 100.0 / (close * syminfo.pointvalue * 1.0015)
            rawQty = math.min(riskQty, capQty)
            qty = math.floor(rawQty / syminfo.mincontract) * syminfo.mincontract
            if qty > 0
                strategy.entry("L", strategy.long, qty=qty, alert_message="LONG_FILL")
                // Relative stop is attached before entry fills. The entry candle
                // remains protected without calc_on_order_fills lookahead.
                strategy.exit("Protect", from_entry="L", loss=stopTicks, alert_message="STOP_FILL")

if strategy.position_size > 0 and barstate.isconfirmed
    fixedStop = strategy.position_avg_price - initialDistance
    protectiveStop := na(protectiveStop) ? fixedStop : math.max(protectiveStop, fixedStop)
    if useTrailing and not na(confirmedATR)
        protectiveStop := math.max(protectiveStop, close - stopMultiple * confirmedATR)
    strategy.exit("Protect", from_entry="L", stop=protectiveStop, alert_message="STOP_FILL")
    if exitSignal or not inWindow
        strategy.close("L", alert_message="EXIT_FILL")

plot(signalFast, "Signal fast EMA (forming between signal closes)", color=color.teal)
plot(signalSlow, "Signal slow EMA (forming between signal closes)", color=color.orange)
plot(strategy.position_size > 0 ? protectiveStop : na, "Protective stop", color=color.red, style=plot.style_linebr)
plotshape(ready and inWindow and enterSignal and strategy.position_size == 0 and cooldown == 0,
     title="Entry decision; fills next chart open", style=shape.triangleup,
     location=location.belowbar, color=color.teal, size=size.tiny)
// Tested standard charts: 5m, 10m, 1h, 4h, 1D. With 4h signals on a daily chart,
// only the final confirmed 4h state at daily close can trigger the daily order.
// Alert type: Order fills. Message: {{strategy.order.alert_message}}
'''
source=source.replace('"SIGNALTF"','"'+('240' if c['signal_minutes']==240 else '1D')+'"')
source=source.replace('input.int(FAST,','input.int('+str(c['length'])+',')
source=source.replace('input.float(STOP,','input.float('+str(c['stop_atr'])+',')
source=source.replace('input.bool(TRAIL,','input.bool('+str(c['trailing']).lower()+',')
source=source.replace('input.int(ADX,','input.int('+str(c['adx_min'])+',')
source=source.replace('input.int(COOL,','input.int('+str(c['cooldown'])+',')
source=source.replace('// Optimized research candidate.', '// Research candidate: failed broad-market profitability promotion.')
(OUT/'Crypto_Strategy.pine').write_text(source)
print('Exported frozen EMA winner',c['id'])
