def submit_job(store, queue, job):
    queue.acknowledge(job["id"])
    store.save(job)
    return job["id"]
