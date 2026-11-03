/** Compact board help: hover, keyboard focus, touch toggle, and Escape dismissal. */
'use strict';
document.addEventListener('DOMContentLoaded', () => {
  let active = null;
  const close = () => {
    if (!active) return;
    active.querySelector('[role="tooltip"]').hidden = true;
    active.querySelector('.help-trigger').setAttribute('aria-expanded', 'false');
    active = null;
  };
  for (const tip of document.querySelectorAll('.help-tip')) {
    const trigger = tip.querySelector('.help-trigger');
    const content = tip.querySelector('[role="tooltip"]');
    const open = () => {
      close();
      active = tip;
      content.hidden = false;
      trigger.setAttribute('aria-expanded', 'true');
      const rect = trigger.getBoundingClientRect();
      const width = content.getBoundingClientRect().width;
      content.style.left = Math.max(8, Math.min(rect.left, window.innerWidth - width - 8)) + 'px';
      content.style.top = '';
      content.style.bottom = '';
      if (rect.bottom + content.getBoundingClientRect().height <= window.innerHeight - 8) {
        content.style.top = rect.bottom + 'px';
      } else {
        content.style.bottom = Math.max(8, window.innerHeight - rect.top) + 'px';
      }
    };
    tip.addEventListener('mouseenter', open);
    tip.addEventListener('mouseleave', () => {
      if (active === tip && document.activeElement !== trigger) close();
    });
    trigger.addEventListener('focus', open);
    trigger.addEventListener('blur', () => { if (active === tip) close(); });
    // Focus/hover can open help before click; keep the first click open on touch.
    let wasOpen = false;
    trigger.addEventListener('pointerdown', () => { wasOpen = active === tip; });
    trigger.addEventListener('click', event => {
      if (event.detail !== 0 && wasOpen && active === tip) close();
      else open();
    });
  }
  document.addEventListener('keydown', event => { if (event.key === 'Escape') close(); });
  document.addEventListener('pointerdown', event => {
    if (active && !active.contains(event.target)) close();
  });
  window.addEventListener('resize', close);
  // Fixed-position help must not linger over a scrolled-away control.
  document.addEventListener('scroll', event => {
    if (active && !active.querySelector('[role="tooltip"]').contains(event.target)) close();
  }, true);
});