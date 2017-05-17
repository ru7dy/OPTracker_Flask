#!/usr/bin/python

from __future__ import print_function

import os
import time
import urllib
import urllib2
import json
import ssl
import sys
import socket
import random

from urlparse import urlparse
from bs4 import BeautifulSoup
from random import randint
from time import sleep
from functools import wraps


def retry(ExceptionToCheck, tries=4, delay=3, backoff=2, logger=None):
    """Retry calling the decorated function using an exponential backoff.

    http://www.saltycrane.com/blog/2009/11/trying-out-retry-decorator-python/
    original from: http://wiki.python.org/moin/PythonDecoratorLibrary#Retry

    :param ExceptionToCheck: the exception to check. may be a tuple of
        exceptions to check
    :type ExceptionToCheck: Exception or tuple
    :param tries: number of times to try (not retry) before giving up
    :type tries: int
    :param delay: initial delay between retries in seconds
    :type delay: int
    :param backoff: backoff multiplier e.g. value of 2 will double the delay
        each retry
    :type backoff: int
    :param logger: logger to use. If None, print
    :type logger: logging.Logger instance
    """
    def deco_retry(f):

        @wraps(f)
        def f_retry(*args, **kwargs):
            mtries, mdelay = tries, delay
            while mtries > 1:
                try:
                    return f(*args, **kwargs)
                except ExceptionToCheck, e:
                    msg = "%s, Retrying in %d seconds..." % (str(e), mdelay)
                    if logger:
                        logger.warning(msg)
                    else:
                        print(msg, file=sys.stderr)
                    time.sleep(mdelay)
                    mtries -= 1
                    mdelay *= backoff
            return f(*args, **kwargs)

        return f_retry  # true decorator

    return deco_retry


@retry(Exception, tries=8, delay=5, backoff=2)
def urlopen_robust_no_ssl_warning(req):
    # Ignore the SSL verification error
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    # urlopen might timeout
    response = urllib2.urlopen(req, context=ctx)
    # response read might timeout
    return response.read()


@retry(Exception, tries=8, delay=5, backoff=2)
def urlopen_robust(req):
    # urlopen might timeout
    response = urllib2.urlopen(req)
    # response read might timeout
    return response.read()


def getStatusText(receipt_num):

    url = 'https://egov.uscis.gov/casestatus/mycasestatus.do'

    values = {
        'changeLocale' : None,
        'completedActionsCurrentPage' : 0,
        'upcomingActionsCurrentPage' : 0,
        'appReceiptNum' : receipt_num,
        'caseStatusSearchBtn' : 'CHECK+STATUS'
    }

    data = urllib.urlencode(values)
    req = urllib2.Request(url, data)

    response_page = urlopen_robust(req)

    soup = BeautifulSoup(response_page, 'html.parser')
    status = soup.find('div', {'class': 'col-lg-12 appointment-sec center'})
    return status.find('p').text


def getApplicationStatus(receipt_num):

    status_object = {}
    # the timestamp is in minutes
    status_object['receipt'] = receipt_num
    status_object['timestamp'] = int(time.time())

    raw_status = getStatusText(receipt_num)
    if len(raw_status) == 0:
        raw_status = 'NA.'
    status_object['text'] = raw_status

    return status_object


def install_proxy(https_proxy_addr):
    if not https_proxy_addr:
        raise ValueError('Proxy address is empty')
    proxy = urllib2.ProxyHandler({'https': https_proxy_addr})
    opener = urllib2.build_opener(proxy)
    urllib2.install_opener(opener)


def get_current_ip():
    https_url = 'https://ip.42.pl/short'
    https_response = urllib2.urlopen(https_url)
    https_response_page = https_response.read();
    https_soup = BeautifulSoup(https_response_page, 'html.parser')
    return https_soup.prettify().strip()


def verify_proxy(https_proxy_addr, original_ip):
    if not https_proxy_addr:
        raise ValueError('Proxy address is empty')
    current_ip = get_current_ip()
    if current_ip == original_ip:
        raise RuntimeError('Proxy not working:\t' + https_proxy_addr)
    return current_ip


@retry(Exception, tries=5, delay=5, backoff=1)
def install_and_verify_proxy(https_proxies, original_ip):
    if not https_proxies:
        print('No Proxy available', file=sys.stderr)
        return
    https_proxy_addr = random.choice(https_proxies)
    install_proxy(https_proxy_addr)
    new_ip = verify_proxy(https_proxy_addr, original_ip)
    print('HTTPS Proxy activated:\t' + https_proxy_addr + " with new ip:\t" + new_ip, file=sys.stderr)


def sampleOPTCases(start, end, sample_interval, delay, https_proxies, reset_count):

    opt_header = 'YSC1790'

    if not os.path.exists('./tmp'):
        os.makedirs('./tmp')

    timestamp = int(time.time())
    status_file = open('./tmp/' + str(start).zfill(6) + '-' + str(timestamp) + '.dat', 'w')

    original_ip = get_current_ip()

    socket.setdefaulttimeout(10)
    count = 0

    while start + count * sample_interval < end:
        # change proxy after a few queries
        if count % reset_count == 0:
            install_and_verify_proxy(https_proxies, original_ip)

        sample_receipt_num = opt_header + str(start + count * sample_interval).zfill(6)
        # wait 5-10 seconds
        random_delay = randint(delay, 2 * delay)
        print('wait... ' + str(random_delay), file=sys.stderr)
        time.sleep(random_delay)

        print('query:\t' + sample_receipt_num, file=sys.stderr)
        status_object = getApplicationStatus(sample_receipt_num)
        print('result:\t' + str(status_object), file=sys.stderr)

        json.dump(status_object, status_file, indent=4)
        status_file.write('\n')
        status_file.flush()
        count += 1

    status_file.close()


#proxies = ['us.proxymesh.com:31280']
#proxies = ['us-ca.proxymesh.com:31280']
#proxies = ['us-ny.proxymesh.com:31280']
#proxies = ['us-il.proxymesh.com:31280']
#proxies = ['us-fl.proxymesh.com:31280']
#proxies = ['us-dc.proxymesh.com:31280']
#proxies = ['us-wa.proxymesh.com:31280']

#sampleOPTCases(0, 60000, 10, 3, proxies, 100)
