def submit_job(store, queue, job):
    store.save(job)
    queue.acknowledge(job["id"])
    return job["id"]
