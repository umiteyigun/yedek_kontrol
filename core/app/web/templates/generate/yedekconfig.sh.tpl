#!/bin/bash
###########---SABIT_TANIMLAR---#################
# Bu dosya web panel tarafindan otomatik uretilir.
ORACLE_VER={{ oracle_ver }}
ORACLE_SID={{ oracle_sid }}
#Hastahane adi
hastane={{ hastane }}
il={{ il }}
#system sifresi
password={{ password }}
directory={{ directory }}
directorydizini={{ directorydizini }}

hostname={{ hostname }}
kurumkodu={{ kurumkodu }}
################################################
##---Local_FTP_Ayarlar---##
localftpip='{{ localftpip }}'
localftpuser='{{ localftpuser }}'
localftppass='{{ localftppass }}'
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
ORACLE_SID=$ORACLE_SID; export ORACLE_SID
ORACLE_TERM=xterm; export ORACLE_TERM
PATH=/usr/sbin:$PATH; export PATH
PATH=$ORACLE_HOME/bin:$PATH; export PATH
tarih=`date +\%Y\%m\%d\%H`;
trtektarih=`date +\%Y\%m\%d`;
yedektipi=$1
yedekleme=$yedektipi'YEDEK'
kurum=$hastane$yedekleme;
kurumadi=$kurum$tarih;
dosyaadi=$kurumadi".";
dmpdosyaadi=$dosyaadi"dmp";
logdosyaadi=$dosyaadi"log";
rardosyaadi=$dosyaadi"rar";
lgwdosyaadi=$dosyaadi"lgw";
tardosyaadi=$dosyaadi"tar.gz"
gzipdosyaadi=$dosyaadi"dmp.gz";
tarihformat=`date --date='1 day ago' +%Y%m%d%H`;

ftplog1=/yedek/config/ftp-remote.log
ftplog2=/yedek/config/ftp-upload.log
