#!/usr/bin/env python

import os
import re
import sys
import xlrd
import time
import pickle
import random
import subprocess
import xml.etree.ElementTree as ET

assert sys.version_info[0] == 3

ADB_PATH = "adb"
NEXT_BUTTON_POS = (900, 2150)   #TODO needs to change this if using another phone, extracted from uiautomatorviewer

search_db = {}
current_number = 0
correct_ans = {}
CORRECT_ANS_FILE = "correct.pkl"

ERROR_INJECT_COUNT = 3
err_inject_lst = []

ans2idx = lambda x: "ABCDEF".index(x.upper()) + 1


def invoke_adb_shell_cmd(cmd):
    cmdlst = [ADB_PATH]
    cmdlst.extend(cmd)
    return subprocess.check_output(cmdlst, stderr=subprocess.STDOUT).decode("utf-8")


def acquire_ui_xml(test_mode):
    if not test_mode:
        while True:
            r = invoke_adb_shell_cmd(("shell", "uiautomator", "dump"))
            if "ERROR: null root node returned by UiTestAutomationBridge" not in r:
                break
            time.sleep(0.5)

        mobj = re.match(r"UI hierchary dumped to:\s*(\S+)", r)
        if not mobj:
            raise RuntimeError("uiautomator dump error: " + str(r))
        xml = mobj.group(1)
        r = invoke_adb_shell_cmd(("pull", xml, "tmp.xml"))
        if not "file pulled" in r:
            raise RuntimeError("adb pull error: " + str(r))
    return ET.parse("tmp.xml").getroot()


# question_id = 0     # start from 0
def parse_title_number(xml_root):
    # global question_id
    # question_id += 1
    # return question_id

    # txt = xml_root.findall(".//*[@resource-id='com.ruobilin.medical:id/tv_title']")[0].get("text")
    # return int(re.search(r"(\d+)", txt).group(1))

    txt = xml_root.findall(".//*[@resource-id='com.ruobilin.medical:id/framelayout']")[0][0][0].get("text")
    return int(re.search(r"(\d+)\/100", txt).group(1))


def parse_content(xml_root, number):
    index = 0
    for node in xml_root.findall(".//*[@resource-id='pnlSelectTemplate']"):
        index += 1
        if index == number:
            return node
    return None


class Answer(object):

    def __init__(self, node):
        self.text = node.get("text").strip()
        self.position = self.parse_bounds(node.get("bounds"))

    @staticmethod
    def parse_bounds(txt):
        """[x,y][x,y]"""
        count = 0
        x = 0
        y = 0
        for t in re.findall(r"\[\s*(\d+)\s*,\s*(\d+)\s*\]", txt):
            x += int(t[0])
            y += int(t[1])
            count += 1
        assert count == 2
        x /= count
        y /= count
        return x, y

    def __str__(self):
        return "\t|%s|-(%d,%d)"%(self.text, self.position[0], self.position[1])


def record_error(question, ans):
    print("record correct ans for: %s|%s"%(question, str(ans)))
    correct_ans[question] = ans
    with open(CORRECT_ANS_FILE, "wb") as fout:
        pickle.dump(correct_ans, fout)


def process_question():
    global current_number
    global err_inject_lst

    xml = acquire_ui_xml(False)

    q_number = parse_title_number(xml)

    if q_number < current_number and q_number == 10:
        q_number = 100
    current_number = q_number

    node = parse_content(xml, q_number)

    q_type = node.findall(".//*[@resource-id='lblTitleType']")[0].get("text")
    q_question = node.findall(".//*[@resource-id='lblTitle']")[0].get("text").strip()
    if q_question.find("、") >= 0:
        q_question = q_question[q_question.find("、") + 1:].strip()

    error_ans = node.findall(".//*[@resource-id='pnlCorrentContent']")
    if error_ans:
        ans = [ans2idx(s.strip()) for s in error_ans[0].get("text").split(":")[1].split(";")]
        record_error(q_question, ans)
        do_tap(*NEXT_BUTTON_POS)
        return True

    q_answers = []

    print(q_type)
    print("|%d|%s|"%(q_number, q_question))
    
    for ans in node.findall(".//*[@resource-id='btnSelectTemplate']"):
        q_answers.append(Answer(ans))
        print(q_answers[-1])

    selection = do_search(q_question)
    if not selection:
        print("Answer not found, use default")
        selection = (1,)
        record_error(q_question, (random.randint(1, len(q_answers)),))

    if q_number in err_inject_lst:
        print("Inject error ans: %d"%q_number)
        selection = (1,)

    print(selection)
    for i in selection:
        do_tap(*q_answers[i - 1].position)

    if len(selection) > 1 or q_type == "多选题":
        # needs to press "next" button
        do_tap(*NEXT_BUTTON_POS)

    return True


def do_search(question):
    question = question.strip()
    if question in search_db:
        return search_db[question]
    if question in correct_ans:
        return correct_ans[question]
    return ()


def do_tap(x, y):
    invoke_adb_shell_cmd(["shell", "input", "tap", "%d"%x, "%d"%y])


def parse_xls(path, skip_rows, question_col, answer_col):
    rst = {}
    book = xlrd.open_workbook(path)
    for i in range(book.nsheets):
        count = 0
        sheet = book.sheet_by_index(i)
        for row_num in range(skip_rows, sheet.nrows):
            name = sheet.cell(row_num, question_col).value.strip()
            ans = sheet.cell(row_num, answer_col).value

            if isinstance(ans, float) or isinstance(ans, int):
                value = (int(ans),)
            elif isinstance(ans, str) and "," in ans:
                value = [int(s) for s in ans.split(",") if s.strip()]
            elif isinstance(ans, str) and "、" in ans:
                value = [int(s) for s in ans.split("、") if s.strip()]
            elif isinstance(ans, str) and re.match(r"[a-fA-F]+", ans.strip()):
                value = []
                for c in ans.strip():
                    if c not in "ABCDEF":
                        continue
                    value.append(ans2idx(c))
            else:
                raise RuntimeError("Unknown text from %s Sheet%d line%d: %s" % (path, i, row_num + 1, ans))
            rst[name] = value
            count += 1
        print("Sheet%d found %d items"%(i, count))
    print("Size of %s is %d"%(path, len(rst)))
    return rst


def add_db_file(path, skip_rows, question_col, ans_col):
    db = parse_xls(path, skip_rows, question_col, ans_col)
    for key in db:
        search_db[key] = db[key]


if __name__ == "__main__":
    if os.path.exists(CORRECT_ANS_FILE):
        with open(CORRECT_ANS_FILE, "rb") as fin:
            correct_ans = pickle.load(fin)
            print("Load %d correct ans"%len(correct_ans))
    add_db_file("ganranke_1.xlsx", 2, 1, 2)
    add_db_file("jinhumao.xls", 1, 1, 0)

    for i in range(random.randint(1, ERROR_INJECT_COUNT)):
        d = random.randint(1, 100)
        if d in err_inject_lst:
            continue
        err_inject_lst.append(d)
    print("Errs: %s"%(repr(err_inject_lst)))

    # for k in search_db:
    #     print(k, search_db[k])
    while True:
        if process_question():
            #time.sleep(1.5)   # wait for UI refresh for exam
            time.sleep(1)


