#include <iostream>
#include <bits/stdc++.h>
#include "publisher/publisher.h"
#include "subscriber-l/subscriber.h"
#include "subscriber-s/subscriber.h"
#include "common/tsc_clock.h"
using namespace std;

int main(int argc, char* argv[]) {
    if(argc <2){
        return -1;
    }

    string role = argv[1];
    if(role == "publisher-shm"){
        if (!pin_to_cpu(2)) {
            perror("sched_setaffinity");
            return -1;
        }
        init_tsc_clock();
        cout<<"Starting Publisher (SHM)"<<endl;
        Publisher pub(PublisherMode::SHM_ONLY);
        pub.run();
        return 0;
    }
    else if(role == "publisher-socket"){
        if (!pin_to_cpu(2)) {
            perror("sched_setaffinity");
            return -1;
        }
        init_tsc_clock();
        cout<<"Starting Publisher (Socket)"<<endl;
        Publisher pub(PublisherMode::SOCKET_ONLY);
        pub.run();
        return 0;
    }
    else if(role == "publisher-both" || role == "publisher"){
        if (!pin_to_cpu(2)) {
            perror("sched_setaffinity");
            return -1;
        }
        init_tsc_clock();
        cout<<"Starting Publisher (Both)"<<endl;
        Publisher pub(PublisherMode::BOTH);
        pub.run();
        return 0;
    }
    else if(role == "subscriber-shm" || role == "subscriber-shared-memory"){
        if (!pin_to_cpu(3)) {
            perror("sched_setaffinity");
            return -1;
        }
        init_tsc_clock();
        cout<<"Starting Subscriber shared memory"<<endl;
        SubscriberSharedMemory sub_s;
        sub_s.run();
        return 0;
    }
    else if(role == "subscriber-socket" || role == "subscriber-loopback"){
        if (!pin_to_cpu(4)) {
            perror("sched_setaffinity");
            return -1;
        }
        init_tsc_clock();
        cout<<"Starting Subscriber socket"<<endl;
        SubscriberLoopback sub_l;
        sub_l.run();
        return 0;
    }
    else{
        cout<< "Unknown role: " << role <<endl;
        return -1;
    }

}