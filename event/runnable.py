'''
Created on 2015/6/16

@author: hubo
'''
from __future__ import print_function, absolute_import, division 
import sys
from .core import QuitException, TimerEvent, SystemControlEvent
from .event import Event, withIndices

class EventHandler(object):
    '''
    Runnable with an event handler model. 
    '''
    def __init__(self, scheduler = None):
        self.handlers = dict()
        self.scheduler = scheduler
    def bind(self, scheduler):
        self.scheduler = scheduler
    def __iter__(self):
        '''
        Keep it like a iterator. Not very useful.
        '''
        return self
    def next(self):
        '''
        Keep it like a iterator. Not very useful.
        '''
        self.send(None)
    def __next__(self):
        '''
        Python 3 next
        '''
        self.send(None)
    def send(self, etup):
        '''
        Handle events
        '''
        return self.handlers[etup[1]](etup[0], self.scheduler)
    def registerHandler(self, matcher, handler):
        '''
        Register self to scheduler
        '''
        self.handlers[matcher] = handler
        self.scheduler.register((matcher,), self)
    def unregisterHandler(self, matcher):
        self.scheduler.unregister((matcher,), self)
        del self.handlers[matcher]
    def registerAllHandlers(self, handlerDict):
        '''
        Register self to scheduler
        '''
        self.handlers.update(handlerDict)
        if hasattr(handlerDict, 'keys'):
            self.scheduler.register(handlerDict.keys(), self)
        else:
            self.scheduler.register(tuple(h[0] for h in handlerDict), self)
    def close(self):
        self.scheduler.unregisterall(self)
    def registerExceptionHandler(self, handler):
        self.exceptionHandler = handler
    def registerQuitHandler(self, handler):
        self.quitHandler = handler
    def throw(self, exc):
        if isinstance(exc, QuitException):
            self.quitHandler(self.scheduler)
        else:
            self.exceptionHandler(exc, self.scheduler)
    def exceptionHandler(self, exc, scheduler):
        raise exc
    def quitHandler(self, scheduler):
        raise StopIteration

@withIndices('type', 'routine')
class RoutineControlEvent(Event):
    canignore = False
    ASYNC_START = 'asyncstart'
    DELEGATE_FINISHED = 'delegatefinished'

class IllegalMatchersException(Exception):
    pass

def Routine(iterator, scheduler, asyncStart = True, container = None, manualStart = False, daemon = False):
    def run():
        iterself, re = yield
        rcMatcher = RoutineControlEvent.createMatcher(RoutineControlEvent.ASYNC_START, iterself)
        if manualStart:
            yield
        try:
            if asyncStart:
                scheduler.register((rcMatcher,), iterself)
                (event, m) = yield
                event.canignore = True
                scheduler.unregister((rcMatcher,), iterself)
            if container is not None:
                container.currentroutine = iterself
            if daemon:
                scheduler.setDaemon(iterself, True)
            matchers = next(iterator)
            try:
                scheduler.register(matchers, iterself)
            except:
                iterator.throw(IllegalMatchersException(matchers))
                raise
            while True:
                try:
                    etup = yield
                except:
                    scheduler.unregister(matchers, iterself)
                    t,v,tr = sys.exc_info()  # @UnusedVariable
                    if container is not None:
                        container.currentroutine = iterself
                    matchers = iterator.throw(t,v)
                else:
                    scheduler.unregister(matchers, iterself)
                    if container is not None:
                        container.event = etup[0]
                        container.matcher = etup[1]
                    if container is not None:
                        container.currentroutine = iterself
                    matchers = iterator.send(etup)
                try:
                    scheduler.register(matchers, iterself)
                except:
                    iterator.throw(IllegalMatchersException(matchers))
                    raise
        finally:
            if asyncStart:
                re.canignore = True
                scheduler.ignore(rcMatcher)
            if container is not None:
                container.currentroutine = iterself
            iterator.close()
            scheduler.unregisterall(iterself)
    r = run()
    next(r)
    if asyncStart:
        re = RoutineControlEvent(RoutineControlEvent.ASYNC_START, r)
        r.send((r, re))
        waiter = scheduler.send(re)
        if waiter is not None:
            # This should not happen regularly
            def latencyStart(w):
                while w:
                    yield (w,)
                    w = scheduler.send(re)
            Routine(latencyStart(waiter), scheduler, False)
    else:
        r.send((r, None))
    return r

class RoutineException(Exception):
    def __init__(self, matcher, event):
        Exception.__init__(self, matcher, event)
        self.matcher = matcher
        self.event = event
    
class RoutineContainer(object):
    def __init__(self, scheduler = None, daemon = False):
        self.scheduler = scheduler
        self.daemon = daemon
    def bind(self, scheduler):
        self.scheduler = scheduler
    def main(self):
        raise NotImplementedError
    def start(self, asyncStart = False):
        r = Routine(self.main(), self.scheduler, asyncStart, self, True, self.daemon)
        self.mainroutine = r
        try:
            next(r)
        except StopIteration:
            pass
        return r
    def subroutine(self, iterator, asyncStart = True, name = None, daemon = False):
        r = Routine(iterator, self.scheduler, asyncStart, self, True, daemon)
        if name is not None:
            setattr(self, name, r)
        try:
            next(r)
        except StopIteration:
            pass
        return r
    def terminate(self, routine = None):
        if routine is None:
            routine = self.mainroutine
        routine.close()
    def waitForSend(self, event):
        '''
        Can call without delegate
        '''
        waiter = self.scheduler.send(event)
        while waiter:
            yield (waiter,)
            waiter = self.scheduler.send(event)
    def waitWithTimeout(self, timeout, *matchers):
        if timeout is None:
            yield matchers
        else:
            th = self.scheduler.setTimer(timeout)
            try:
                tm = TimerEvent.createMatcher(th)
                yield tuple(matchers) + (tm,)
                if self.matcher is tm:
                    self.timeout = True
                else:
                    self.timeout = False
            finally:
                self.scheduler.cancelTimer(th)
    def executeWithTimeout(self, timeout, subprocess):
        if timeout is None:
            for m in subprocess:
                yield m
        else:
            th = self.scheduler.setTimer(timeout)
            try:
                tm = TimerEvent.createMatcher(th)
                try:
                    for m in self.withException(subprocess, tm):
                        yield m
                    self.timeout = False
                except RoutineException as exc:
                    if exc.matcher is tm:
                        self.timeout = True
                    else:
                        raise
            finally:
                self.scheduler.cancelTimer(th)
                subprocess.close()
    def doEvents(self):
        '''
        Can call without delegate
        '''
        self.scheduler.wantContinue()
        cm = SystemControlEvent.createMatcher(SystemControlEvent.CONTINUE)
        yield (cm,)
    def withException(self, subprocess, *matchers):
        try:
            for m in subprocess:
                yield tuple(m) + tuple(matchers)
                if self.matcher in matchers:
                    raise RoutineException(self.matcher, self.event)
        finally:
            subprocess.close()
    def withCallback(self, subprocess, callback, *matchers):
        try:
            for m in subprocess:
                while True:
                    yield tuple(m) + tuple(matchers)
                    if self.matcher in matchers:
                        callback(self.event, self.matcher)
                    else:
                        break
        finally:
            subprocess.close()
                
    def waitForEmpty(self, queue):
        '''
        Can call without delegate
        '''
        while True:
            m = queue.waitForEmpty()
            if m is None:
                break
            else:
                yield (m,)
    def waitForAll(self, *matchers):
        eventdict = {}
        eventlist = []
        matchers = list(matchers)
        while matchers:
            yield tuple(matchers)
            matchers.remove(self.matcher)
            eventlist.append(self.event)
            eventdict[self.matcher] = self.event
        self.eventlist = eventlist
        self.eventdict = eventdict 
    def waitForAllToProcess(self, *matchers):
        eventdict = {}
        eventlist = []
        matchers = list(matchers)
        while matchers:
            yield tuple(matchers)
            matchers.remove(self.matcher)
            self.event.canignore = True
            eventlist.append(self.event)
            eventdict[self.matcher] = self.event
        self.eventlist = eventlist
        self.eventdict = eventdict
    def waitForAllEmpty(self, *queues):
        matchers = [m for m in (q.waitForEmpty() for q in queues) if m is not None]
        while matchers:
            for m in self.waitForAll(*matchers):
                yield m
            matchers = [m for m in (q.waitForEmpty() for q in queues) if m is not None]
    def syscall_noreturn(self, func):
        '''
        Can call without delegate
        '''
        matcher = self.scheduler.syscall(func)
        yield (matcher,)
    def syscall(self, func, ignoreException = False):
        for m in self.syscall_noreturn(func):
            yield m
        if hasattr(self.event, 'exception'):
            raise self.event.exception[1]
        else:
            self.retvalue = self.event.retvalue
    def delegate(self, subprocess):
        def delegateroutine():
            try:
                for m in subprocess:
                    yield m
            except:
                e = RoutineControlEvent(RoutineControlEvent.DELEGATE_FINISHED, self.currentroutine)
                e.canignore = True
                for m in self.waitForSend(e):
                    yield m
                raise
            else:
                e = RoutineControlEvent(RoutineControlEvent.DELEGATE_FINISHED, self.currentroutine)
                e.canignore = True
                for m in self.waitForSend(e):
                    yield m
        r = self.subroutine(delegateroutine(), True)
        finish = RoutineControlEvent.createMatcher(RoutineControlEvent.DELEGATE_FINISHED, r)
        yield (finish,)
