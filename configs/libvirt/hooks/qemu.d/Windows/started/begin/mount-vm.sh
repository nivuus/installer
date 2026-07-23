#!/bin/bash

while ! nc -z 192.168.3.2 139; do   
	sleep 2 # wait for 1/10 of the second before check again
done
mount -t cifs -o credentials=/etc/vm-credential //192.168.3.2/c /media/vm

