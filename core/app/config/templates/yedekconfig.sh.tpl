#!/bin/bash
###########---SUNUCU_SEVIYESI---#################
# Bu dosya yedek-core tarafindan otomatik uretilir.
ORACLE_VER={{ oracle_ver }}
hostname={{ hostname }}
################################################
ORACLE_HOSTNAME=$hostname; export ORACLE_HOSTNAME
ORACLE_BASE=/u01/app/oracle; export ORACLE_BASE
# ORACLE_HOME: oratab, sonra disk (db / db_1)
if [[ -z "${ORACLE_HOME:-}" && -f /etc/oratab ]]; then
  ORACLE_HOME="$(awk -F: '$0 !~ /^#/ && NF>=2 && $2 != "" { print $2; exit }' /etc/oratab)"
fi
if [[ -z "${ORACLE_HOME:-}" || ! -d "${ORACLE_HOME}" ]]; then
  ORACLE_HOME="$(ls -d /u01/app/oracle/product/*/db 2>/dev/null || ls -d /u01/app/oracle/product/*/db_1 2>/dev/null | head -1)"
fi
if [[ -z "${ORACLE_HOME:-}" ]]; then
  ORACLE_HOME="$ORACLE_BASE/product/$ORACLE_VER/db_1"
fi
export ORACLE_HOME
ORACLE_TERM=xterm; export ORACLE_TERM
PATH=/usr/sbin:$PATH; export PATH
PATH=$ORACLE_HOME/bin:$PATH; export PATH
ftplog1=/yedek/config/ftp-remote.log
ftplog2=/yedek/config/ftp-upload.log
