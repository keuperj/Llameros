/**
 * @file Pure catalogue filtering, ranking, and labels for the Llameros admin dashboard.
 * Shared by the browser and dependency-free Node regression tests.
 */
'use strict';
// Pure helpers shared by the browser and dependency-free Node tests.
const Catalogue = {
  /**
   * Build an official-library information URL only for a validated family/tag.
   *
   * @param {string} model - Selected catalogue name.
   * @returns {?string} Official URL or null for invalid input.
   */
  modelInfoUrl(model) {
    if (!/^[a-z0-9][a-z0-9._-]{0,79}(?::[A-Za-z0-9][A-Za-z0-9._-]{0,127})?$/.test(model) || /\s/.test(model)) return null;
    const [family, tag] = model.split(':');
    return `https://ollama.com/library/${family}${tag ? ':' + encodeURIComponent(tag) : ''}`;
  },
  /**
   * Filter variants by tag query and optional CPU fit without mutating cached catalogue entries.
   *
   * @param {Array<Object>} entries - Families with variant lists.
   * @param {string} [query=""] - Current catalogue search.
   * @param {boolean} [cpuOnly=false] - Require an affirmative CPU fit.
   * @returns {Array<Object>} Filtered family copies.
   */
  visible(entries, query = '', cpuOnly = false) {
    const tagQuery = query.trim().toLowerCase();
    return entries.map(entry => ({...entry, variants: entry.variants.filter(variant =>
      (!tagQuery.includes(':') || variant.name.toLowerCase().includes(tagQuery)) &&
      (!cpuOnly || variant.cpu_fits === true))}))
      .filter(entry => (!cpuOnly && !tagQuery.includes(':')) || entry.variants.length > 0);
  },
  /**
   * Sort fit before non-fit before unknown, using upstream rank to break ties.
   *
   * @param {Array<Object>} entries - Fit/rank annotated entries.
   * @returns {Array<Object>} Sorted copy.
   */
  sort(entries) {
    const priority = value => value === true ? 0 : value === false ? 1 : 2;
    return [...entries].sort((a, b) => priority(a.fits) - priority(b.fits) || a.rank - b.rank);
  },
  /**
   * Choose up to six fitting families in popularity order, preferring a fitting latest tag.
   *
   * @param {Array<Object>} entries - Compatibility-checked families.
   * @returns {Array<Object>} Family/rank/variant recommendations.
   */
  recommendations(entries) {
    return entries.filter(entry => entry.fits === true && entry.variants.some(v => v.fits === true))
      .sort((a, b) => a.rank - b.rank).slice(0, 6).map(entry => ({
        family: entry.name,
        rank: entry.rank,
        variant: entry.variants.find(v => v.fits === true && v.name.endsWith(':latest')) || entry.variants.find(v => v.fits === true),
      }));
  },
  /**
   * Format an advisory fit marker without presenting estimates as guaranteed compatibility.
   *
   * @param {Object} entry - Name and tri-state fits value.
   * @returns {string} Plain-text selector label.
   */
  label(entry) {
    return `${entry.fits === true ? '● ' : entry.fits === false ? '× ' : '? '}${entry.name}${entry.fits === true ? ' — estimated fit' : entry.fits === false ? ' — exceeds estimate' : ' — unchecked / unknown'}`;
  },
};
if (typeof module !== 'undefined') module.exports = Catalogue;