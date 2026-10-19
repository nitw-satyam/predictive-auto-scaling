from locust import HttpUser, task, between

class AutoScalingUser(HttpUser):
    wait_time = between(0.2, 1.0)

    @task
    def hello(self):
        self.client.get("/health")