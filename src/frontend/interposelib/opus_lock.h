#ifndef SRC_FRONTEND_INTERPOSELIB_OPUS_LOCK_H_
#define SRC_FRONTEND_INTERPOSELIB_OPUS_LOCK_H_

#include <pthread.h>

class OPUSLock
{
    public:
        OPUSLock() {};
        virtual void acquire() {};
        virtual void release() {};
        virtual void acquire_rdlock() {};
        virtual void acquire_wrlock() {};
        virtual void wait() {};
        virtual void notify() {};
        virtual void destroy_lock() {};
        virtual ~OPUSLock() = 0;
};

class SimpleLock : public OPUSLock
{
    public:
        SimpleLock();
        ~SimpleLock();

        void acquire();
        void release();
        void destroy_lock();

    protected:
        pthread_mutex_t simple_lock;
        pthread_mutexattr_t mutex_attr;

    private:
        SimpleLock(const SimpleLock&);
        SimpleLock& operator=(const SimpleLock&);
};

class ConditionLock : public SimpleLock
{
    public:
        ConditionLock();
        ~ConditionLock();

        void wait();
        void notify();
        void destroy_lock();

    private:
        ConditionLock(const ConditionLock&);
        ConditionLock& operator=(const ConditionLock&);

        pthread_cond_t cond;
};


class ReadWriteLock : public OPUSLock
{
    public:
        enum LockType { READ_LOCK, WRITE_LOCK };

        ReadWriteLock();
        ~ReadWriteLock();

        void acquire_rdlock();
        void acquire_wrlock();
        void release();
        void destroy_lock();

    private:
        ReadWriteLock(const ReadWriteLock&);
        ReadWriteLock& operator=(const ReadWriteLock&);

        pthread_rwlock_t rwlock;
};

#endif  // SRC_FRONTEND_INTERPOSELIB_OPUS_LOCK_H_
