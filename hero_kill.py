"""Step 6 -- the stronger claim.

The sweep kills the PROCESS (os._exit inside a write). This kills the MACHINE: a real
sandbox.stop() lands while the workflow is holding a file open mid-write, then the same
machine is restarted and the surviving wreckage is inspected.

Do not conflate the two on stage: "the sweep kills the process; this one kills the machine."
"""
import json, time
from dotenv import load_dotenv

load_dotenv("/home/mx/daytona_hacksprint/.env")
from daytona import Daytona, CreateSandboxFromSnapshotParams, SessionExecuteRequest

d = Daytona()
sb = d.create(CreateSandboxFromSnapshotParams(auto_stop_interval=0))
log = {"sandbox_id": sb.id}
print("sandbox:", sb.id)
try:
    sb.fs.upload_file(open("workflow.py", "rb").read(), "workflow.py")

    # launch, holding the process open inside the ledger write
    sb.process.create_session("run")
    sb.process.execute_session_command(
        "run", SessionExecuteRequest(command="HOLD=25 python workflow.py > run.log 2>&1",
                                     run_async=True))
    time.sleep(8)
    mid = sb.fs.download_file("ledger.txt").decode()
    print(f"  mid-flight ledger: {mid!r}")
    log["ledger_before_kill"] = mid

    print("  >>> sandbox.stop() -- killing the MACHINE, not the process")
    t = time.time(); sb.stop(); log["stop_seconds"] = round(time.time() - t, 1)
    print(f"  stopped in {log['stop_seconds']}s, state={sb.state}")
    log["state_while_down"] = str(sb.state)

    print("  >>> sandbox.start() -- same machine, same disk")
    t = time.time(); sb.start(); log["start_seconds"] = round(time.time() - t, 1)
    print(f"  started in {log['start_seconds']}s")

    survived = sb.fs.download_file("ledger.txt").decode()
    print(f"  ledger AFTER machine kill + restart: {survived!r}")
    log["ledger_after_restart"] = survived
    log["state_survived_machine_death"] = (survived == mid and not survived.endswith("\n"))

    # restart the agent on the machine its predecessor died on
    r = sb.process.exec("FAULT=ok python workflow.py", timeout=60)
    final = sb.fs.download_file("ledger.txt").decode()
    log["ledger_after_agent_restart"] = final
    log["refunds"] = final.count("REFUND")
    print(f"  after restarting the agent: {final!r}")
    print(f"  REFUND records: {log['refunds']}")
finally:
    json.dump(log, open("hero_kill.json", "w"), indent=2)
    sb.delete()
    print("sandbox deleted; log -> hero_kill.json")
