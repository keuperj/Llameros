'use strict';
// Pure helpers shared by the browser and dependency-free Node tests.
const Catalogue = {
  sort(entries) {
    const priority = value => value === true ? 0 : value === false ? 1 : 2;
    return [...entries].sort((a, b) => priority(a.fits) - priority(b.fits) || a.rank - b.rank);
  },
  recommendations(entries) {
    return entries.filter(entry => entry.fits === true && entry.variants.some(v => v.fits === true))
      .sort((a, b) => a.rank - b.rank).slice(0, 6).map(entry => ({
        family: entry.name,
        rank: entry.rank,
        variant: entry.variants.find(v => v.fits === true && v.name.endsWith(':latest')) || entry.variants.find(v => v.fits === true),
      }));
  },
  label(entry) {
    return `${entry.fits === true ? '● ' : entry.fits === false ? '× ' : '? '}${entry.name}${entry.fits === true ? ' — estimated fit' : entry.fits === false ? ' — exceeds estimate' : ' — unchecked / unknown'}`;
  },
};
if (typeof module !== 'undefined') module.exports = Catalogue;