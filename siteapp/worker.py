import threading
import time
from .scanner import run_scan

class Worker:
    def __init__(self,store,cfg):
        self.store=store; self.cfg=cfg; self.stop_event=threading.Event(); self.thread=None
    def start(self):
        if self.thread and self.thread.is_alive(): return
        self.thread=threading.Thread(target=self._loop,name='bounded-scan-worker',daemon=True)
        self.thread.start()
    def stop(self):
        self.stop_event.set()
        if self.thread: self.thread.join(timeout=2)
    def _loop(self):
        next_cleanup=0
        while not self.stop_event.is_set():
            try:
                if time.monotonic()>next_cleanup:
                    self.store.cleanup(); next_cleanup=time.monotonic()+60
                job=self.store.claim_job()
                if not job:
                    self.stop_event.wait(.5); continue
                try:
                    result,status,reason=run_scan(job['domain'],self.cfg,lambda n:self.store.progress(job['id'],n))
                except Exception:
                    result={'index':None,'coverage':0,'checks':[],'evidence':[],'unknowns':['Внутренняя ошибка обработки. Результат не подтверждён.'],'pages_processed':0}
                    status='failed'; reason='Обработка не завершилась. Демонстрационные данные не использовались.'
                self.store.finish(job['id'],result,status,reason)
            except Exception:
                self.stop_event.wait(1)
