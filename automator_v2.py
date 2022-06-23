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
NEXT_BUTTON_POS = (1100, 2200)   #TODO adapt this on other phone, extracted from uiautomatorviewer

search_db = {
    "单选题":{},
    "多选题":{},
    "判断题":{},
}
current_number = 0
correct_ans = {}
for key in search_db:
    correct_ans[key] = {}

CORRECT_ANS_FILE = "correct.pkl"
xml_path = None

ERROR_INJECT_COUNT = 0          #TODO The number of errors to be injected
err_inject_lst = []
last_q_number = None

ans2idx = lambda x: "ABCDEF".index(x.upper()) + 1


def invoke_adb_shell_cmd(cmd):
    cmdlst = [ADB_PATH]
    cmdlst.extend(cmd)
    return subprocess.check_output(cmdlst, stderr=subprocess.STDOUT).decode("utf-8")


def acquire_ui_xml():
    global xml_path
    while True:
        xml = invoke_adb_shell_cmd(("shell", "uiautomator dump; cat %s"%xml_path))
        if "UI hierchary dumped to:" in xml:
            return ET.fromstring(xml[xml.find("<?"):])


def parse_title_number(xml_root):
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
        return "\t%s"%(self.text)


def record_error(q_type, question, ans):
    print("record correct ans for: %s|%s|%s"%(q_type, question, str(ans)))
    assert q_type in correct_ans
    if q_type == "单选题" and len(ans) != 1:
        raise RuntimeError("invalid ans(%s) for question %s"%(str(ans), question))
    if q_type == "判断题" and len(ans) != 1:
        raise RuntimeError("invalid ans(%s) for question %s"%(str(ans), question))
    correct_ans[q_type][question] = ans

    with open(CORRECT_ANS_FILE, "wb") as fout:
        pickle.dump(correct_ans, fout)


def process_question():
    global err_inject_lst
    global last_q_number

    xml = acquire_ui_xml()

    q_number = parse_title_number(xml)

    node = parse_content(xml, q_number)

    q_type = node.findall(".//*[@resource-id='lblTitleType']")[0].get("text").strip()
    q_question = node.findall(".//*[@resource-id='lblTitle']")[0].get("text").strip()
    if q_question.find("、") >= 0:
        q_question = q_question[q_question.find("、") + 1:].strip()

    error_ans = node.findall(".//*[@resource-id='pnlCorrentContent']")
    if error_ans:
        ans = [ans2idx(s.strip()) for s in error_ans[0].get("text").split(":")[1].split(";")]
        record_error(q_type, q_question, ans)
        do_tap(NEXT_BUTTON_POS)
        return True

    if q_number == last_q_number:
        print("waiting for UI refresh...[%d]"%q_number)
        return False
    last_q_number = q_number

    q_answers = []

    print(q_type)
    print("%d.%s"%(q_number, q_question))
    
    for ans in node.findall(".//*[@resource-id='lblOptionContent']"):
        q_answers.append(Answer(ans))
        print(q_answers[-1])

    selection = do_search(q_type, q_question)
    if not selection:
        print("Answer not found, use default")
        selection = (1,)
        record_error(q_type, q_question, (random.randint(1, len(q_answers)),))

    if q_number in err_inject_lst:
        print("Inject error ans: %d"%q_number)
        selection = (1,)

    print(selection)
    do_tap(*(q_answers[i - 1].position for i in selection))

    if len(selection) > 1 or q_type == "多选题":
        # needs to press "next" button
        do_tap(NEXT_BUTTON_POS)

    return True


def do_search(q_type, question):
    if question in correct_ans[q_type]:
        return correct_ans[q_type][question]
    if question in search_db[q_type]:
        return search_db[q_type][question]
    return ()


def do_tap(*argv):
    cmds = []
    for arg in argv:
        cmds.append("input tap %d %d"%(arg[0], arg[1]))
    invoke_adb_shell_cmd(["shell", ";".join(cmds)])


def parse_xls(path, skip_rows, question_type_col, question_col, answer_col):
    result = {
        "单选题":{},
        "多选题":{},
        "判断题":{},
    }
    book = xlrd.open_workbook(path)
    for i in range(book.nsheets):
        count = 0
        sheet = book.sheet_by_index(i)

        if question_type_col is None:
            sheet_name = sheet.name.strip()
            if sheet_name == "单选":
                q_type = "单选题"
            elif sheet_name == "多选":
                q_type = "多选题"
            else:
                raise RuntimeError("unsupport sheet(%d) name(%s)"%(i, sheet.name.strip()))

        for row_num in range(skip_rows, sheet.nrows):
            if question_type_col is not None:
                q_type = sheet.cell(row_num, question_type_col).value.strip()
                if q_type not in result:
                    raise RuntimeError("unsupport sheet(%d), row(%d), type(%s)"%(i, row_num, q_type))

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

            result[q_type][name] = value
            count += 1
        #print("Sheet%d found %d items"%(i, count))
    print("%s:\t%s"%(path, ", ".join(["%s:%d"%(s, len(result[s])) for s in result])))
    return result


def add_db_file(path, skip_rows, question_type_col, question_col, ans_col):
    ret = parse_xls(path, skip_rows, question_type_col, question_col, ans_col)

    def copy_rst(dst, src):
        for key in src:
            dst[key] = src[key]
    
    copy_rst(search_db["单选题"], ret["单选题"])
    copy_rst(search_db["多选题"], ret["多选题"])
    copy_rst(search_db["判断题"], ret["判断题"])


if __name__ == "__main__":
    if os.path.exists(CORRECT_ANS_FILE):
        with open(CORRECT_ANS_FILE, "rb") as fin:
            correct_ans = pickle.load(fin)
            print("Load correct ans: %s"%(",".join(["%s:%d"%(s, len(correct_ans[s])) for s in correct_ans])))

    add_db_file("ganranke_1.xlsx", 2, 0, 1, 2)
    add_db_file("jinhumao.xls", 1, None, 1, 0)
    add_db_file("jianhushi.xlsx", 2, 0, 1, 2)
    add_db_file("1.xlsx", 1, 0, 1, 2)

    print("Total\t%s"%(",".join(["%s:%d"%(s, len(search_db[s])) for s in search_db])))

    if ERROR_INJECT_COUNT > 0:
        for i in range(random.randint(1, ERROR_INJECT_COUNT)):
            d = random.randint(1, 100)
            if d in err_inject_lst:
                continue
            err_inject_lst.append(d)
        print("Errs: %s"%(repr(err_inject_lst)))

    r = invoke_adb_shell_cmd(("shell", "uiautomator dump"))
    sobj = re.search(r'UI hierchary dumped to:\s*(\S+)', r)
    assert sobj
    xml_path = sobj.group(1)
    print("UI dump path: %s"%xml_path)

    while True:
        if process_question():
            #time.sleep(2)   # wait for UI refresh for exam
            pass
