function isRelevantJob(job) {
  const titleAndDesc = `${job.title || ""} ${job.description || ""}`.toLowerCase();
  const locationHaystack = `${job.city || ""} ${job.location || ""} ${job.url || ""}`.toLowerCase();

  const hasLocation = LOCATION_WHITELIST.some((term) =>
    locationHaystack.includes(term)
  );
  if (!hasLocation) {
    return false;
  }

  const hasJobType = JOB_WHITELIST.some((term) => titleAndDesc.includes(term));
  if (!hasJobType) {
    return false;
  }

  return !JOB_BLACKLIST.some((term) => titleAndDesc.includes(term));
}