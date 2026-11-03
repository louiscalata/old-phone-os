# Worn Battery Results

*Old Phone OS Interface is GPL-3.0.*

What was measured: 40 simulated phones with a worn battery (health 0.3-0.4, temperatures 0-45 C, start 60-100%), run by `PYTHONPATH=src python3 -m opos.evaleol`. "before" = the phone runs its normal way; "after" = OPOS keeps a battery reserve for calls, shapes call and sync bursts to what the worn cell can deliver, and has a planned-shutdown rule (should_shutdown). That rule never triggered in these runs: for health 0.3-0.4 its shutdown_floor is 0% at every temperature the runs reached, so every "after" phone ran its simulated battery down to 0% without a brownout.

Event definition: brownout = simulated cell voltage < 3.0 V (CUTOFF_V = 3.0) under load, causing immediate simulated power loss. Planned shutdown = termination of the simulation when should_shutdown reaches the shutdown_floor threshold; call_reserve instead determines reserve protection for tasks.

Planned-shutdown accounting: died_at% reports average state-of-charge at brownout; for "after" modes with 0 brownouts% died_at% is 0.0, not the charge at planned shutdown. brownouts% excludes planned shutdowns. Neither zero brownouts% nor the simulator's termination flag demonstrates controlled shutdown on real hardware.

Tested resistance assumptions: the simulated cell uses fixed r0 = 0.2 ohm (`src/opos/sim.py:eol_sim`); its health is varied from 0.3 to 0.4. The reserve, burst-shaping and shutdown policy separately estimates r0_for_health = 0.05 + 0.23*(1-health). Both use `src/opos/eol.py`'s temperature factor 1+0.03*(25-temp) below 25°C and 1+0.02*(temp-35) above 35°C, and SoC factor 1+3*(1-q/100)^3. No contact aging or transient load spikes beyond the modeled bursts are included.

Results v1:
~~~
policy  mode    calls_h sms_h alarms_h app_h alive_h brownouts% died_at%
basic   before     0.76  0.76     0.76  0.76    0.76        100     24.6
basic   after      1.91  2.03     2.03  1.65    2.03          0      0.0
static  before     1.62  1.62     1.62  1.62    1.62        100     20.0
static  after      2.74  2.86     2.86  2.48    2.86          0      0.0
rules   before     3.67  3.67     3.67  3.67    3.67        100     18.8
rules   after      4.73  4.84     4.84  4.49    4.84          0      0.0
router  before     5.52  5.52     5.52  5.52    5.52        100     18.7
router  after      6.54  6.69     6.69  6.31    6.69          0      0.0
~~~

How to read it: the "brownouts%" column is the share of phones that cut out suddenly mid-use; "died_at%" is the average charge level at that point. Before, all 40 simulated worn phones (100%) cut out suddenly, at an average of about 19-25% charge depending on the policy (individual phones anywhere from 0% to 34%); after, 0%. Zero brownouts% in this simulation does not by itself demonstrate controlled shutdown on real hardware.

Average hours of working calls over the 40 simulated worn phones, without and then with OPOS's worn-battery reserve and burst shaping: plain phone 0.76 and 1.91, static low-power 1.62 and 2.74, rules 3.67 and 4.73, rule router 5.52 and 6.54. The router with reserve and burst shaping gives 6.54 hours of calls against 2.74 for static low-power with reserve and burst shaping.

Limits: this is a simulation of a worn battery, not a measurement on a real worn phone; a held-out seed is checked by tests/test_evaleol.py; the numbers come from one battery model (src/opos/eol.py).
