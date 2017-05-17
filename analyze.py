from __future__ import print_function

import os
import json
import re
import time
import sys
import glob
import pandas
import numpy

from pandas.tseries.holiday import USFederalHolidayCalendar
from pandas.tseries.offsets import CustomBusinessDay

from datetime import datetime
from datetime import time
from datetime import timedelta
from query import getApplicationStatus
from query import install_proxy


class StatusMeta:
    def __init__(self):
        self.type = None
        self.status = None
        self.date = None


class Prediction:
    def __init__(self):
        self.code = None
        self.estimate = None
        self.info = None
        self.pending = None
        self.speed = None
        self.change = None
        self.bucket_progress = None


def getStatusObjects(path):

    status_list = []

    fin = open(path, 'r')
    lines = list(fin)

    for i in range(0, len(lines)/5):
        status_text = ''.join(lines[5*i: 5*i+5])
        status_list.append(json.loads(status_text))

    fin.close()
    return status_list


def parseStatusText(receiptNum, content):

    current_status = StatusMeta()
    current_status.type = 'UNKNOWN'
    current_status.status = 'UNKNOWN'

    content = content.strip()

    if content == 'NA.' or not content.startswith('On'):
        return current_status

    # handle special cases (appeal)
    if content.startswith('Your appeal was dismissed'):
        records = content.split(',')
        date_str = records[3][3:] + ',' + records[4]
        status_str = records[0]

    elif content.startswith('On'):
        records = content.split(',')
        date_str = records[0][3:] + ',' + records[1]
        status_str = records[2].strip()

    date = datetime.strptime(date_str, '%B %d, %Y')
    current_status.date = date

    if status_str.startswith('Your appeal was dismissed'):
        current_status.status = 'APPEAL FAILED'
    elif status_str == 'we received your Form I-765':
        current_status.status = 'RECEIVED'
    elif status_str.startswith('we approved your Form I-765'):
        current_status.status = 'ISSUED & MAILED'
    elif status_str.startswith('we mailed your new card for Receipt Number'):
        current_status.status = 'ISSUED & MAILED'
    elif status_str.startswith('the Post Office delivered your new card for'):
        current_status.status = 'DELIVERED'
    elif status_str.startswith('we updated your'):
        current_status.status = 'INFORMATION UPDATED'
    elif status_str.startswith('the check you used for payment for your Form I-765'):
        current_status.status = 'PAYMENT FAILURE'
    elif status_str.startswith('we ordered your new card for Receipt Number'):
        current_status.status = 'ORDERED'
    elif status_str.startswith('the Post Office returned a notice we sent you for your Form I-765'):
        current_status.status = 'NOTICE FAILURE'
    elif status_str.startswith('we mailed a request for initial evidence for your Form I-765'):
        current_status.status = 'REQUEST EVIDENCE'
    elif status_str.startswith('we received your request to withdraw your Form I-765'):
        current_status.status = 'WITHDRAWED'
    elif status_str.startswith('the Post Office picked up mail containing your new card for Receipt Number'):
        current_status.status = 'ISSUED & READY FOR MAIL'
    elif status_str.startswith('we received your correspondence for Form I-765'):
        current_status.status = 'CORRESPONDENCE RECEIVED'
    elif status_str.startswith('we transferred your Form I-765'):
        current_status.status = 'CASE TRANSFERRED'
    elif status_str.startswith('we received your response to our Request for Evidence for your Form I-765'):
        current_status.status = 'EVIDENCE RECEIVED'
    elif status_str.startswith('the Post Office returned your new card for Receipt Number'):
        current_status.status = 'DELIVERY FAILURE'
    elif status_str.startswith('we rejected your Form I-765'):
        current_status.status = 'REJECTED'

    if current_status.status != 'UNKNOWN':
        current_status.type = 'I-765'
    else:
        current_status.type = 'OTHERS'
        current_status.status = status_str

    # debugging information
    # if current_status.status == 'UNKNOWN':
    #   print(receiptNum + '\t' + str(date) + '\t' + current_status.status + '\t' + status_str, file=sys.stderr)

    return current_status


def getAggregatedStatus(version):

    status_by_time = {}
    status_by_receipt = {}

    status_files = sorted(glob.glob('*.dat'))

    if abs(version) > len(status_files):
        return None, None

    latest_status_file = status_files[version]
    timestamp = datetime.fromtimestamp(int(latest_status_file.split('.')[0]))

    for status_object in getStatusObjects(latest_status_file):
        meta = parseStatusText(status_object['receipt'], status_object['text'])
        status_by_receipt[status_object['receipt']] = meta

        if meta.type == 'I-765':
            if meta.date not in status_by_time:
                status_by_time[meta.date] = {}
            if meta.status not in status_by_time[meta.date]:
                status_by_time[meta.date][meta.status] = 0
            status_by_time[meta.date][meta.status] += 1

    return timestamp, status_by_receipt, status_by_time


def updateStat(sequence, status_by_receipt, opt_finished_cases, opt_cases, other_cases, unknown_cases):
    receipt = 'YSC1790' + str(sequence).zfill(6)
    bucket = int(sequence / 5000)

    if sequence % 5000 == 0:
        if bucket not in opt_cases:
            opt_cases[bucket] = 0
        if bucket not in opt_finished_cases:
            opt_finished_cases[bucket] = 0
        if bucket not in other_cases:
            other_cases[bucket] = 0
        if bucket not in unknown_cases:
            unknown_cases[bucket] = 0

    if receipt in status_by_receipt:
        if status_by_receipt[receipt].type == 'I-765':
            opt_cases[bucket] += 1
            if status_by_receipt[receipt].status in ['ISSUED & MAILED', 'DELIVERED', 'WITHDRAWED']:
                opt_finished_cases[bucket] += 1
        elif status_by_receipt[receipt].type == 'OTHERS':
            other_cases[bucket] += 1
        elif status_by_receipt[receipt].type == 'UNKNOWN':
            unknown_cases[bucket] += 1
    else:
        # The cases we haven't crawled yet
        unknown_cases[bucket] += 1


def estimate(receipt_num, version, history_length):
    """ Returns an estimate of the issued date
    receipt_num: a 13 digit sequence assigned when each case is received by USCIS
    version: an integer representing the statistics we used: -1 corresponds to the lastest
    history_length: the length (num of business days) we use in the prediction
    """

    result = Prediction()
    pattern = re.compile('YSC1790(\d{6})$')

    if not pattern.match(receipt_num):
        result.code = 'UNEXPECTED RECEIPT FORMAT'
        result.info = """The reciept number you entered has an invalid
        format. Please double check. Note that we only accept receipt
        number starting with YSC because all newly filed I-765 from F1
        applicants will now be processed in Potomac Service Center. """
        return result

    timestamp, status_by_receipt, status_by_time = getAggregatedStatus(version)

    # First check the current status of the case and handle special cases
    install_proxy('us.proxymesh.com:31280')
    current_status = getApplicationStatus(receipt_num)
    current_status_meta = parseStatusText(current_status['receipt'], current_status['text'])

    if current_status_meta.type in ['OTHERS']:
        result.code = 'RECEIPT NOT FOR I-765'
        result.info = 'The receipt number you entered does not correspond to I-765 application.'
        return result

    if current_status_meta.status in ['UNKNOWN']:
        result.info = 'USCIS may have sent you a paper receipt, but has not input your case into the system yet.'
    else:
        result.info = current_status['text']

    # If the case has already been issued, simply return
    if current_status_meta.status in ['ISSUED & MAILED', 'DELIVERED']:
        result.code = 'ALREADY ISSUED'
        return result

    if current_status_meta.status in ['REJECTED']:
        result.code = 'ALREADY REJECTED'
        return result

    # Else, let's make a prediction
    max_sequence = int(receipt_num[7:])

    opt_finished_cases = {}
    opt_cases = {}
    other_cases = {}
    unknown_cases = {}

    last_sequence = 0

    # Handle all sequences before the input
    for sequence in range(0, max_sequence + 1, 10):
        last_sequence = sequence
        updateStat(sequence, status_by_receipt, opt_finished_cases, opt_cases, other_cases, unknown_cases)

    # Calculate the remaining cases before you
    ratio = float(sum(opt_cases.values())) / (sum(opt_cases.values()) + sum(other_cases.values()))
    pending_cases = sum(opt_cases.values()) - sum(opt_finished_cases.values()) + int(ratio * sum(unknown_cases.values()))

    # Handle the rest of the last bucket
    for sequence in range(last_sequence, (max_sequence / 5000 + 1) * 5000, 10):
        updateStat(sequence, status_by_receipt, opt_finished_cases, opt_cases, other_cases, unknown_cases)

    # Calculate the progress in each bucket
    bucket_progress = {}
    for bucket in opt_cases:
        bucket_progress[bucket] = int(opt_finished_cases[bucket] * 100 / (opt_cases[bucket] + unknown_cases[bucket] * ratio))

    #today = pandas.datetime.today()
    today = timestamp

    if today.hour < 17:
        today = today - timedelta(days=1)

    skipped_days = 0
    past_days = 0
    past_issued = []

    result.code = 'OK'

    for day in range(0, history_length):

        # if no issue in the last week, stop prediction
        if skipped_days >= 3 and past_days == 3:
            result.code = 'EXPIRED DATASET'
            return result
        if past_days >= 3 and sum(past_issued) == 0:
            result.code = 'INSUFFICIENT INFORMATION'

        history = datetime.combine((today - day * CustomBusinessDay(calendar=USFederalHolidayCalendar())).date(), time.min)

        if history in status_by_time:
            past_days += 1
            issued_cases = 0
            # This if condition is added in case no case was issued at a certain day
            if 'ISSUED & MAILED' in status_by_time[history]:
                issued_cases = status_by_time[history]['ISSUED & MAILED']
            past_issued.append(issued_cases)
            print('[History]:\t' + str(history) + '\t' + str(issued_cases), file=sys.stderr)
        elif history < timestamp:
            # no updates on this day
            past_days += 1
            issued_cases = 0
            past_issued.append(issued_cases)
            print('[History]:\t' + str(history) + '\t' + str(issued_cases), file=sys.stderr)
        else:
            print('[Skipping]:\t' + str(history), file=sys.stderr)
            skipped_days += 1

    past_week_speed = numpy.mean(past_issued[len(past_issued)/2: len(past_issued)])
    current_week_speed = numpy.mean(past_issued[0: len(past_issued)/2])
    change = 100 * (current_week_speed - past_week_speed) / max(past_week_speed, 1)
    result.change = int(change)

    past_speed = sum(past_issued) / past_days
    estimated_days = pending_cases / past_speed

    print('', file=sys.stderr)
    print('pending cases:\t' + str(pending_cases), file=sys.stderr)
    print('speed (/day):\t' + str(past_speed), file=sys.stderr)
    print('speed change:\t' + str(int(change)) + '%', file=sys.stderr)
    print('estimated days:\t' + str(estimated_days), file=sys.stderr)
    print('', file=sys.stderr)

    result.estimate = today + (estimated_days + 1) * CustomBusinessDay(calendar=USFederalHolidayCalendar())
    result.pending = pending_cases * 10
    result.speed = past_speed * 10
    result.bucket_progress = bucket_progress

    return result

#prediction = estimate('YSC1790095015', -1, 10)