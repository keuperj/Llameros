'use strict';
// Stable action identities survive table refreshes; never retain request bodies/secrets.
const ActionFeedback = {
  create({document, log, notice}) {
    const errors = new Map(), pending = new Map();
    function identity(control) {
      return control.dataset.feedbackKey || control.id;
    }
    function bind(control, key) {
      if (key) control.dataset.feedbackKey = key;
      paint(control);
      return control;
    }
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
    function clear(control) {
      if (!control) return;
      errors.delete(identity(control)); paint(control);
    }
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
    function restore() {
      document.querySelectorAll('button, select, input[type=checkbox]').forEach(paint);
    }
    function track(control, job) {
      if (control && job.job_id) pending.set(job.job_id, identity(control));
    }
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
    function reset() { errors.clear(); pending.clear(); restore(); }
    return {bind, clear, fail, restore, track, job, reset};
  },
};
if (typeof module !== 'undefined') module.exports = ActionFeedback;