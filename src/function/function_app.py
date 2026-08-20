import logging

import azure.functions as func

from capacity_inventory import run_from_environment


app = func.FunctionApp()


@app.timer_trigger(
    schedule="%TIMER_SCHEDULE%",
    arg_name="timer",
    run_on_startup=False,
    use_monitor=True,
)
def collect_compute_capacity(timer: func.TimerRequest) -> None:
    if timer.past_due:
        logging.warning("The compute capacity inventory timer is running late.")

    logging.info("Starting daily Azure compute capacity inventory.")
    record_count = run_from_environment()
    logging.info("Published %d compute capacity records.", record_count)
