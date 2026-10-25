/**
 * @file Action feedback and lifecycle-job tracking for the Llameros dashboard.
 * Stable control IDs preserve errors across refreshes without retaining request secrets.
 */
'use strict';
// Stable action identities survive table refreshes; never retain request bodies/secrets.
const ActionFeedback = {
  /**
   * Create a page-local feedback controller; maps hold only control IDs and safe error strings.
   *
   * @param {Object} dependencies - document, log callback, and notice callback.
   * @returns {Object} Methods for binding controls, tracking jobs, reporting errors, and reset.
   */
  create({document, log, notice}) {
    const errors = new Map(), pending = new Map();
    /**
     * Resolve a stable feedback identity that survives control recreation.
     *
     * @param {HTMLElement} control - Bound control.
     * @returns {string} Explicit feedback key or DOM ID.
     */
    function identity(control) {
      return control.dataset.feedbackKey || control.id;
    }
    /**
     * Associate a recreated control with a stable action identity and repaint its error badge.
     *
     * @param {HTMLElement} control - Control to register.
     * @param {string} key - Stable action ID.
     * @returns {HTMLElement} The original control.
     */
    function bind(control, key) {
      if (key) control.dataset.feedbackKey = key;
      paint(control);
      return control;
    }
    /**
     * Create/update an adjacent accessible error badge without injecting HTML.
     *
     * @param {HTMLElement} control - Currently mounted control.
     */
    function paint(control) {
      const key = identity(control);
      if (!key || !control.parentNode) return;
      let badge = control.nextElementSibling;
      if (!badge || !badge.classList.contains('action-error')) {
        if (!errors.has(key)) return;
        badge = document.createElement('span');
        badge.className = 'action-error'; badge.setAttribute('role', 'alert');
        control.insertAdjacentElement('afterend', badge);
      }
      badge.textContent = errors.get(key) || ''; badge.hidden = !errors.has(key);
    }
    /**
     * Clear one control's error before retrying; absent controls are harmless.
     *
     * @param {?HTMLElement} control - Retried control.
     */
    function clear(control) {
      if (!control) return;
      errors.delete(identity(control)); paint(control);
    }
    /**
     * Show local/global feedback, reveal collapsed controls, and optionally persist safe metadata.
     *
     * @param {?HTMLElement} control - Originating control, if known.
     * @param {string} message - Safe display text.
     * @param {boolean} [persist=true] - Whether the server still needs this event.
     */
    function fail(control, message, persist = true) {
      const key = control && identity(control);
      if (key) errors.set(key, message);
      if (control) {
        const details = control.closest && control.closest('details');
        if (details) details.open = true;
        paint(control);
        restore();
      }
      notice(message);
      log({action: key || 'dashboard', message}, persist);
    }
    /**
     * Repaint feedback after dashboard refreshes replace buttons, selects, or checkboxes.
     */
    function restore() {
      document.querySelectorAll('button, select, input[type=checkbox]').forEach(paint);
    }
    /**
     * Associate a submitted job with its action control until a terminal status arrives.
     *
     * @param {?HTMLElement} control - Originating control.
     * @param {Object} job - Initial snapshot with job_id.
     */
    function track(control, job) {
      if (control && job.job_id) pending.set(job.job_id, identity(control));
    }
    /**
     * Resolve terminal job feedback without duplicating worker-persisted failure events.
     *
     * @param {Object} job - Latest lifecycle snapshot.
     */
    function job(job) {
      const key = pending.get(job.job_id);
      if (!key || job.state === 'running') return;
      pending.delete(job.job_id);
      if (job.state === 'failed') {
        const message = job.error || 'Operation failed';
        errors.set(key, message); notice(message);
        // Lifecycle worker already persists this error; avoid duplicate server events.
        log({action: key, message}, false);
        restore();
      }
    }
    /**
     * Forget pending IDs/errors and repaint on sign-out so no prior-session feedback survives.
     */
    function reset() { errors.clear(); pending.clear(); restore(); }
    return {bind, clear, fail, restore, track, job, reset};
  },
};
if (typeof module !== 'undefined') module.exports = ActionFeedback;