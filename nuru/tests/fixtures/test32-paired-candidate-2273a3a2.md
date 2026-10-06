Based on the notes I’ve read, here is the audit of your three systems and the status of the 3-year battery requirement.

### 1. Battery Life Modeling (nuru/battery-life-modeling-revised.md)
*   **Scenario A (Always-connected / eDRX):**
    *   **Current:** ~5.35 mA.
    *   **Life:** ~237 days (~8 months).
    *   **Verdict:** **Fails** the 3-year requirement by a factor of ~5.
*   **Scenario B (Periodic attach / Modem off):**
    *   **Naive Estimate:** ~19.9 years (using 1.53 Ah/yr).
    *   **Realistic Adjustments:**
        *   Self-discharge (1%/yr) reduces capacity to ~27.4 Ah by year 10.
        *   Cold temp brownout + signal retries increases charge per report from 602 mC to an expected 804 mC (40% increase).
        *   New charge/year: ~2.02 Ah/yr.
    *   **Revised Life:** ~13.6 years (capped by shelf life at ~10 years).
    *   **Verdict:** **Passes** the 3-year requirement *only if* the device enters Periodic Attach mode.
    *   **Sensitivity:** If reporting frequency increases from 1 to 6/hour, life drops to ~2.3 years (**Fails**).

### 2. PSM Timer Decoding (shared/debugging-session-psm-not-engaging.md)
*   **Goal:** Enter Power Saving Mode (PSM).
*   **Current Status:** **Failing.**
*   **Issue:** The network overrides your requested T3324 (60s active timer) with 10s.
*   **Root Cause:** The network-side T3324 is set to 10s (likely a default or APN-specific limit).
*   **Secondary Issue:** Even if T3324 were correct, UPSV=0 (UART power saving disabled) prevents deep sleep. Current draw remains ~12 mA instead of ~3 µA.
*   **Verdict:** **Unresolved.** PSM is not engaging. The device stays in the "Always-connected" state (Scenario A), which has a ~237-day life.

### 3. NAT Keepalive Fix (shared/Projects/area-monitor-device-redesign.md)
*   **Observation:** The modem drops PDP context after ~47 minutes.
*   **Cause:** Operator NAT idle timeout (~45–50 min), confirmed by operator support. Not a firmware bug.
*   **Current Configuration:** MQTT keepalive = 60s.
*   **Analysis:**
    *   If keepalive (60s) > NAT timeout (47 min), the connection drops.
    *   You need `keepalive < T_nat / 2`.
    *   Current: 60s < 2820s / 2 (1410s). This is fine for *keeping* the connection alive.
    *   **However**, the drop happens after 47 min of *idle*. The keepalive is only 1 min. The device is idle for 47 min, then wakes, sends a keepalive, and the network kills the context. The keepalive is too infrequent to prevent the drop, or the "idle" timer starts when the last packet is sent, and the keepalive isn't frequent enough to reset it before the 47 min expiry.
    *   **Proposed Fix:** Increase keepalive to 600s (10 min) as per your daily log.
    *   **Margin:** 2820s / 600s = 4.7x. This is sufficient to reset the NAT timer.
*   **Verdict:** **Resolved.** Increasing keepalive to 600s should prevent the 47-minute drop.

### Summary: Can we hit 3 years?
**No, not yet.**

1.  **The PSM blocker:** Because PSM is not engaging (due to T3324 override and UPSV=0), the device is stuck in the "Always-connected" mode.
2.  **The Result:** The battery life is limited to **~237 days** (Scenario A).
3.  **The Path to 3 Years:** You must fix PSM engagement.
    *   Fix T3324 encoding (you fixed the encoding bug earlier, but the network still overrides to 10s).
    *   Fix UPSV (UART power saving) to allow deep sleep.
    *   **Then** verify the 47-min NAT drop is handled (keepalive=600s).
    *   **Finally** confirm the device actually enters PSM and stays there (not just T3324=10s active mode).

### What is still unresolved / unchecked?
*   **T3324 Override:** The network consistently overrides your 60s request to 10s. Is there an APN difference between bench and field? (Dana is unsure).
*   **UPSV Fix:** You haven't confirmed if setting `AT+UPSV=1` actually allows the module to enter deep sleep given the UART activity.
*   **Reporting Frequency:** The "sensitivity" test (6 reports/hour) was not run. If the product team needs faster reporting, the 3-year target becomes impossible even with PSM.
*   **Interaction:** The PSM fix and the NAT fix are sequential. You can't hit 3 years with PSM off. The NAT fix (keepalive=600s) is a prerequisite for PSM to work reliably in the field, but it doesn't solve the PSM engagement problem itself.

**Biggest Remaining Risk:** **PSM will not engage.** Until the network agrees to a longer active timer (T3324 > 10s) and the UART is powered down (UPSV=1), the device will remain in the high-drain always-connected state, yielding only ~8 months of battery life.