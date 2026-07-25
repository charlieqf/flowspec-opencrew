const FILTER_KEYS = ["analysisStatus", "subtitleMode", "duration", "tag", "updated", "orientation"];

export const MEDIA_LIBRARY_FILTER_DEFAULTS = Object.freeze({
  analysisStatus: "all",
  subtitleMode: "all",
  duration: "all",
  tag: "all",
  updated: "all",
  orientation: "all",
  sort: "updated_desc",
  includeArchived: false,
});

export function mediaLibraryActiveFilterCount(filters = {}) {
  const selectedFilters = FILTER_KEYS.reduce((count, key) => count + (filters[key] && filters[key] !== "all" ? 1 : 0), 0);
  return selectedFilters
    + (filters.sort && filters.sort !== MEDIA_LIBRARY_FILTER_DEFAULTS.sort ? 1 : 0)
    + (filters.includeArchived ? 1 : 0);
}

export function mediaLibraryHasQueryFilters(filters = {}) {
  return Boolean(String(filters.q || "").trim())
    || FILTER_KEYS.some((key) => filters[key] && filters[key] !== "all")
    || Boolean(filters.includeArchived);
}

export function resetMediaLibraryFilters(filters = {}) {
  return {
    ...filters,
    ...MEDIA_LIBRARY_FILTER_DEFAULTS,
    page: 1,
  };
}
