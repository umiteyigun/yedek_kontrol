#!/bin/bash
###########---SUNUCU_SEVIYESI---#################
# Bu dosya yedek-core tarafindan otomatik uretilir.
ORACLE_VER={{ oracle_ver }}
hostname={{ hostname }}
################################################
ORACLE_HOSTNAME=$hostname; export ORACLE_HOSTNAME
ORACLE_BASE=/u01/app/oracle; export ORACLE_BASE
ORACLE_HOME=$ORACLE_BASE/product/$ORACLE_VER/db_1; export ORACLE_HOME
ORACLE_TERM=xterm; export ORACLE_TERM
PATH=/usr/sbin:$PATH; export PATH
PATH=$ORACLE_HOME/bin:$PATH; export PATH
ftplog1=/tmp/remoteftplogfile
ftplog2=/tmp/ftplofile
