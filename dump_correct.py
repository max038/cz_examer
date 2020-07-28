import pickle

count = 0
with open("correct.pkl", "rb") as fin:
    correct_ans = pickle.load(fin)
    stat = {}
    for key in correct_ans:
        stat[key] = 0
        print(key)
    for typ in correct_ans:
        typ_ans = correct_ans[typ]
        for key in typ_ans:
            print("%s\t%s\t(%s)"%(typ, key, ",".join(("%d"%i for i in typ_ans[key]))))
            stat[typ] += 1

print("\n".join(("%s:%d"%(k, stat[k]) for k in stat)))