# -*- coding: utf-8 -*-
"""pinyin_lite.py —— 纯标准库的「汉字 → 无声调拼音音节」查表（官网拼音候选的唯一用表）。

【这个文件是生成的，不要手改】改它请改 ``debug/gen_pinyin_lite.py`` 再重新生成；
``bash debug/run_offline_regression.sh`` 里有对拍判据，手改或表与生成器分家会当场判红。
生成时用的是 pypinyin 0.55.0 的默认读音（多音字取最常见一读），版本换代会改变
候选域名，所以读音必须钉死在这一份表上——理由见生成脚本的文件头。

运行时只依赖标准库：表是两段按 GB2312 码位顺序排列的音节串，字符本身由码位反解
（不需要把 6.7 千字也写进源码，源码因此只有音节那一份）。
"""
from __future__ import annotations

__all__ = ["syllable", "lazy_pinyin", "is_han", "is_syllable", "COVERAGE"]

# 覆盖范围（由生成器按实表字数写入，不在这里手写第二份数字）
COVERAGE = 6763
_ROWS = (
    (
        'a,a,ai,ai,ai,ai,ai,ai,ai,ai,ai,ai,ai,ai,ai,an,an,an,an,an,an,an,an,an,ang,ang,ang,ao,ao,ao' +
        ',ao,ao,ao,ao,ao,ao,ba,ba,ba,ba,ba,ba,ba,ba,ba,ba,ba,ba,ba,ba,ba,ba,ba,ba,bai,bai,bai,bai,b' +
        'ai,bai,bai,bai,ban,ban,ban,ban,ban,ban,ban,ban,ban,ban,ban,ban,ban,ban,ban,bang,bang,bang,' +
        'bang,bang,bang,bang,bang,bang,bang,bang,bang,bao,bao,bao,bao,bo,bao,bao,bao,bao,bao,bao,ba' +
        'o,bao,bao,bao,bao,bao,bei,bei,bei,bei,bei,bei,bei,bei,bei,bei,bei,bei,bei,bei,bei,ben,ben,' +
        'ben,ben,beng,beng,beng,beng,beng,beng,bi,bi,bi,bi,bi,bi,bi,bi,bi,bi,bi,bi,bi,bi,bi,bi,bi,b' +
        'i,bi,pi,bi,bi,bi,bi,bian,bian,bian,bian,bian,bian,bian,bian,bian,bian,bian,bian,biao,biao,' +
        'biao,biao,bie,bie,bie,bie,bin,bin,bin,bin,bin,bin,bing,bing,bing,bing,bing,bing,bing,bing,' +
        'bing,bo,bo,bo,bo,bo,bo,bo,bo,bo,bo,bo,bo,bo,bo,bo,bo,bo,po,bo,bu,bo,bu,bu,bu,bu,bu,bu,bu,b' +
        'u,bu,ca,cai,cai,cai,cai,cai,cai,cai,cai,cai,cai,cai,can,can,can,can,can,can,can,cang,cang,' +
        'cang,cang,cang,cao,cao,cao,cao,cao,ce,ce,ce,ce,ce,ceng,ceng,cha,cha,cha,cha,cha,cha,cha,ch' +
        'a,cha,cha,cha,chai,chai,chai,chan,can,chan,chan,chan,chan,chan,chan,chan,chan,chang,chang,' +
        'chang,chang,chang,zhang,chang,chang,chang,chang,chang,chang,chang,chao,chao,chao,chao,chao' +
        ',chao,chao,chao,chao,che,che,che,che,che,che,chen,chen,chen,chen,chen,chen,chen,chen,chen,' +
        'chen,cheng,cheng,cheng,cheng,cheng,cheng,cheng,cheng,cheng,cheng,cheng,cheng,cheng,cheng,c' +
        'heng,chi,chi,chi,shi,chi,chi,chi,chi,chi,chi,chi,chi,chi,chi,chi,chi,chong,chong,chong,cho' +
        'ng,chong,chou,chou,chou,chou,chou,chou,chou,chou,chou,chou,chou,chou,chu,chu,chu,chu,chu,c' +
        'hu,chu,chu,chu,chu,chu,chu,chu,chu,chu,chu,chuai,chuan,chuan,chuan,chuan,chuan,chuan,chuan' +
        ',chuang,chuang,chuang,chuang,chuang,chuang,chui,chui,chui,chui,chui,chun,chun,chun,chun,ch' +
        'un,chun,chun,chuo,chuo,ci,ci,ci,ci,ci,ci,ci,ci,ci,ci,ci,ci,cong,cong,cong,cong,cong,cong,c' +
        'ou,cu,cu,cu,cu,cuan,cuan,cuan,cui,cui,cui,cui,cui,cui,cui,cui,cun,cun,cun,cuo,cuo,cuo,cuo,' +
        'cuo,cuo,da,da,da,da,da,da,dai,dai,dai,dai,dai,dai,dai,dai,dai,dai,dai,dai,dan,dan,dan,dan,' +
        'dan,dan,dan,dan,dan,dan,dan,dan,dan,dan,dan,dang,dang,dang,dang,dang,dao,dao,dao,dao,dao,d' +
        'ao,dao,dao,dao,dao,dao,dao,de,de,de,deng,deng,deng,deng,deng,deng,deng,di,di,di,di,di,di,d' +
        'i,di,di,di,di,di,di,di,di,di,di,di,di,dian,dian,dian,dian,dian,dian,dian,dian,dian,dian,di' +
        'an,dian,dian,dian,dian,dian,diao,diao,diao,diao,diao,diao,diao,diao,diao,die,die,die,die,d' +
        'ie,die,die,ding,ding,ding,ding,ding,ding,ding,ding,ding,diu,dong,dong,dong,dong,dong,dong,' +
        'dong,dong,dong,dong,dou,dou,dou,dou,dou,dou,dou,dou,du,du,du,du,du,du,du,du,du,du,du,du,du' +
        ',du,duan,duan,duan,duan,duan,duan,dui,dui,dui,dui,dun,dun,dun,dun,dun,dun,dun,dun,dun,duo,' +
        'duo,duo,duo,duo,duo,duo,duo,duo,duo,duo,duo,e,e,e,e,e,e,e,e,e,e,e,e,e,en,er,er,er,er,er,er' +
        ',er,er,fa,fa,fa,fa,fa,fa,fa,fa,fan,fan,fan,fan,fan,fan,fan,fan,fan,fan,fan,fan,fan,fan,fan' +
        ',fan,fan,fang,fang,fang,fang,fang,fang,fang,fang,fang,fang,fang,fei,fei,fei,fei,fei,fei,fe' +
        'i,fei,fei,fei,fei,fei,fen,fen,fen,fen,fen,fen,fen,fen,fen,fen,fen,fen,fen,fen,fen,feng,fen' +
        'g,feng,feng,feng,feng,feng,feng,feng,feng,feng,feng,feng,feng,feng,fu,fou,fu,fu,fu,fu,fu,f' +
        'u,fu,fu,fu,fu,fu,fu,fu,fu,fu,fu,fu,fu,fu,fu,fu,fu,fu,fu,pu,fu,fu,fu,fu,fu,fu,fu,fu,fu,fu,f' +
        'u,fu,fu,fu,fu,fu,fu,fu,fu,fu,ga,ga,gai,gai,gai,gai,gai,gai,gan,gan,gan,gan,gan,gan,gan,gan' +
        ',gan,gan,gan,gang,gang,gang,gang,gang,gang,gang,gang,gang,gao,gao,gao,gao,gao,gao,gao,gao,' +
        'gao,gao,ge,ge,ge,ge,ge,ge,ge,ge,ge,ge,ge,ha,ge,ge,ge,ge,ge,gei,gen,gen,geng,geng,geng,geng' +
        ',geng,geng,geng,gong,gong,gong,gong,gong,gong,gong,gong,gong,gong,gong,gong,gong,gong,gong' +
        ',gou,gou,gou,gou,gou,gou,gou,gou,gou,gu,gu,gu,gu,gu,gu,gu,gu,gu,gu,gu,gu,gu,gu,gu,gu,gu,gu' +
        ',gua,gua,gua,gua,gua,gua,guai,guai,guai,guan,guan,guan,guan,guan,guan,guan,guan,guan,guan,' +
        'guan,guang,guang,guang,gui,gui,gui,gui,gui,gui,gui,gui,gui,gui,gui,gui,gui,gui,gui,gui,gun' +
        ',gun,gun,guo,guo,guo,guo,guo,guo,ha,hai,hai,hai,hai,hai,hai,hai,han,han,han,han,han,han,ha' +
        'n,han,han,han,han,han,han,han,han,han,han,han,han,hang,hang,hang,hao,hao,hao,hao,hao,hao,h' +
        'ao,hao,hao,he,he,he,he,he,he,he,he,he,he,hao,he,he,he,he,he,he,he,hei,hei,hen,hen,hen,hen,' +
        'heng,heng,heng,heng,heng,hong,hong,hong,hong,hong,hong,hong,hong,hong,hou,hou,hou,hou,hou,' +
        'hou,hou,hu,hu,hu,hu,hu,hu,hu,hu,hu,hu,hu,hu,hu,hu,hu,hu,hu,hu,hua,hua,hua,hua,hua,hua,hua,' +
        'hua,hua,huai,huai,huai,huai,huai,huan,huan,huan,hai,huan,huan,huan,huan,huan,huan,huan,hua' +
        'n,huan,huan,huang,huang,huang,huang,huang,huang,huang,huang,huang,huang,huang,huang,huang,' +
        'huang,hui,hui,hui,hui,hui,hui,hui,hui,hui,hui,hui,hui,hui,hui,hui,hui,hui,hui,hui,hui,hui,' +
        'hun,hun,hun,hun,hun,hun,huo,huo,huo,huo,huo,huo,huo,huo,huo,huo,ji,ji,ji,ji,ji,ji,ji,ji,ji' +
        ',ji,ji,ji,ji,ji,ji,ji,ji,ji,ji,ji,ji,ji,ji,ji,ji,ji,ji,ji,ji,ji,ji,ji,ji,ji,ji,ji,ji,ji,ji' +
        ',ji,ji,ji,ji,ji,ji,ji,ji,ji,ji,ji,ji,ji,ji,jia,jia,jia,jia,jia,jia,jia,jia,jia,jia,jia,jia' +
        ',jia,jia,jia,jia,jia,jian,jian,jian,jian,jian,jian,jian,jian,jian,jian,jian,jian,jian,jian' +
        ',jian,jian,jian,jian,jian,jian,jian,jian,jian,jian,kan,jian,jian,jian,jian,jian,jian,jian,' +
        'jian,jian,jian,jian,jian,jian,jian,jian,jiang,jiang,jiang,jiang,jiang,jiang,jiang,jiang,ji' +
        'ang,jiang,jiang,jiang,jiang,jiao,jiao,jiao,jiao,jiao,jiao,jiao,jiao,jiao,jiao,jue,jiao,jia' +
        'o,jiao,jiao,jiao,jiao,jiao,jiao,jiao,jiao,jiao,jiao,jiao,jiao,jiao,jiao,jiao,jie,jie,jie,j' +
        'ie,jie,jie,jie,jie,jie,ju,jie,jie,jie,jie,jie,jie,jie,jie,jie,ji,jie,jie,jie,jie,jie,jie,j' +
        'ie,jin,jin,jin,jin,jin,jin,jin,jin,jin,jin,jin,jin,jin,jin,jin,jin,jin,jin,jin,jin,jing,ji' +
        'ng,jing,jing,jing,jing,jing,jing,jing,jing,jing,jing,jing,jing,jing,jing,jing,jing,jing,ji' +
        'ng,jing,jing,jing,jing,jing,jiong,jiong,jiu,jiu,jiu,jiu,jiu,jiu,jiu,jiu,jiu,jiu,jiu,jiu,ji' +
        'u,jiu,jiu,jiu,jiu,ju,ju,ju,ju,ju,ju,ju,ju,ju,ju,ju,ju,ju,ju,ju,ju,ju,ju,ju,ju,ju,ju,ju,ju,' +
        'ju,juan,juan,juan,juan,juan,juan,juan,jue,jue,jue,jue,jue,jue,jue,jue,jue,jue,jun,jun,jun,' +
        'jun,jun,jun,jun,jun,jun,jun,jun,ka,ka,ka,ge,kai,kai,kai,kai,kai,kan,kan,kan,kan,kan,kan,ka' +
        'ng,kang,kang,kang,kang,kang,kang,kao,kao,kao,kao,ke,ke,ke,ke,ke,ke,ke,ke,ke,ke,ke,ke,ke,ke' +
        ',ke,ken,ken,ken,ken,keng,keng,kong,kong,kong,kong,kou,kou,kou,kou,ku,ku,ku,ku,ku,ku,ku,kua' +
        ',kua,kua,kua,kua,kuai,kuai,kuai,kuai,kuan,kuan,kuang,kuang,kuang,kuang,kuang,kuang,kuang,k' +
        'uang,kui,kui,kui,kui,kui,kui,kui,gui,kui,kui,kui,kun,kun,kun,kun,kuo,kuo,kuo,kuo,la,la,la,' +
        'la,la,la,la,lai,lai,lai,lan,lan,lan,lan,lan,lan,lan,lan,lan,lan,lan,lan,lan,lan,lan,lang,l' +
        'ang,lang,lang,lang,lang,lang,lao,lao,lao,lao,lao,lao,lao,lao,lao,lei,le,lei,lei,lei,lei,le' +
        'i,lei,lei,lei,le,lei,lei,leng,leng,leng,li,li,li,li,li,li,li,li,li,li,li,li,li,li,li,li,li' +
        ',li,li,li,li,li,li,li,li,li,li,li,li,li,li,li,li,li,lia,lian,lian,lian,lian,lian,lian,lian' +
        ',lian,lian,lian,lian,lian,lian,lian,liang,liang,liang,liang,liang,liang,liang,liang,liang,' +
        'liang,liang,liao,liao,liao,liao,liao,liao,liao,lao,le,liao,liao,liao,liao,lie,lie,lie,lie,' +
        'lie,lin,lin,lin,lin,lin,lin,lin,lin,lin,lin,lin,lin,ling,ling,ling,ling,ling,ling,ling,lin' +
        'g,ling,ling,ling,ling,ling,ling,liu,liu,liu,liu,liu,liu,liu,liu,liu,liu,liu,long,long,long' +
        ',long,long,long,long,long,long,lou,lou,lou,lou,lou,lou,lu,lu,lu,lu,lu,lu,lu,lu,lu,lu,lu,lu' +
        ',lu,lu,lu,lu,lu,lu,lu,lu,lv,lv,lv,lv,lv,lv,lv,lv,lv,lv,lv,lv,lv,lv,luan,luan,luan,luan,lua' +
        'n,luan,lve,lve,lun,lun,lun,lun,lun,lun,lun,luo,luo,luo,luo,luo,luo,luo,luo,luo,luo,luo,luo' +
        ',ma,ma,ma,ma,ma,ma,ma,ma,ma,mai,mai,mai,mai,mai,mai,man,man,man,man,man,man,man,man,man,ma' +
        'ng,mang,mang,mang,mang,mang,mao,mao,mao,mao,mao,mao,mao,mao,mao,mao,mao,mao,me,mei,mei,mei' +
        ',mei,mei,mei,mei,mei,mei,mei,mei,mei,mei,mei,mei,mei,men,men,men,meng,meng,meng,meng,meng,' +
        'meng,meng,meng,mi,mi,mi,mi,mi,mi,mi,mi,mi,mi,mi,mi,mi,mi,mian,mian,mian,mian,mian,mian,mia' +
        'n,mian,mian,miao,miao,miao,miao,miao,miao,miao,miao,mie,mie,min,min,min,min,min,min,ming,m' +
        'ing,ming,ming,ming,ming,miu,mo,mo,mo,mo,mo,mo,mo,mo,mo,mo,mo,mo,mo,mo,mo,mo,mo,mou,mou,mou' +
        ',mu,mu,mu,mu,mu,mu,mu,mu,mu,mu,mu,mu,mu,mu,mu,na,na,na,na,na,na,na,nai,nai,nai,nai,nai,nan' +
        ',nan,nan,nang,nao,nao,nao,nao,nao,ne,nei,nei,nen,neng,ni,ni,ni,ni,ni,ni,ni,ni,ni,ni,ni,nia' +
        'n,nian,nian,nian,nian,nian,nian,niang,niang,niao,niao,nie,nie,nie,nie,nie,nie,nie,nin,ning' +
        ',ning,ning,ning,ning,ning,niu,niu,niu,niu,nong,nong,nong,nong,nu,nu,nu,nv,nuan,nve,nve,nuo' +
        ',nuo,nuo,nuo,o,ou,ou,ou,ou,ou,ou,ou,pa,pa,pa,pa,pa,pa,pai,pai,pai,pai,pai,pai,pan,pan,pan,' +
        'pan,pan,pan,pan,pan,pang,pang,pang,pang,pang,pao,pao,pao,pao,pao,pao,pao,pei,pei,pei,pei,p' +
        'ei,pei,pei,pei,pei,pen,pen,peng,peng,peng,peng,peng,peng,peng,peng,peng,peng,peng,peng,pen' +
        'g,peng,pi,pi,pi,pi,pi,pi,pi,pi,pi,pi,pi,pi,pi,pi,pi,pi,pi,pian,pian,pian,pian,piao,piao,pi' +
        'ao,piao,pie,pie,pin,pin,pin,pin,pin,ping,ping,ping,ping,ping,ping,ping,ping,ping,po,po,po,' +
        'po,po,po,po,po,pou,pu,pu,pu,pu,pu,pu,pu,pu,pu,pu,pu,pu,pu,pu,pu,qi,qi,qi,qi,qi,qi,qi,qi,qi' +
        ',qi,qi,qi,qi,qi,qi,qi,qi,qi,qi,qi,qi,qi,qi,qi,qi,qi,qi,qi,qi,qi,qi,qi,qi,qi,qi,qi,qia,qia,' +
        'qia,qian,qian,qian,qian,qian,qian,qian,qian,qian,qian,qian,qian,qian,qian,qian,qian,qian,q' +
        'ian,qian,qian,qian,qian,qiang,qiang,qiang,qiang,qiang,qiang,qiang,qiang,qiao,qiao,qiao,qia' +
        'o,qiao,qiao,qiao,qiao,qiao,qiao,qiao,qiao,qiao,qiao,qiao,qie,jia,qie,qie,qie,qin,qin,qin,q' +
        'in,qin,qin,qin,qin,qin,qin,qin,qing,qing,qing,qing,qing,qing,qing,qing,qing,qing,qing,qing' +
        ',qing,qiong,qiong,qiu,qiu,qiu,qiu,qiu,qiu,qiu,qiu,qu,qu,qu,qu,qu,qu,qu,qu,qu,qu,qu,qu,qu,q' +
        'uan,quan,quan,quan,quan,quan,quan,quan,quan,quan,quan,que,gui,que,que,que,que,que,que,qun,' +
        'qun,ran,ran,ran,ran,rang,rang,rang,rang,rang,rao,rao,rao,re,re,ren,ren,ren,ren,ren,ren,ren' +
        ',ren,ren,ren,reng,reng,ri,rong,rong,rong,rong,rong,rong,rong,rong,rong,rong,rou,rou,rou,ru' +
        ',ru,ru,ru,ru,ru,ru,ru,ru,ru,ruan,ruan,rui,rui,rui,run,run,ruo,ruo,sa,sa,sa,sai,sai,sai,sai' +
        ',san,san,san,san,sang,sang,sang,sao,sao,sao,sao,se,se,se,sen,seng,sha,sha,sha,sha,sha,sha,' +
        'sha,sha,sha,shai,shai,shan,shan,shan,shan,shan,shan,shan,shan,shan,shan,shan,shan,shan,sha' +
        'n,shan,shan,shang,shang,shang,shang,shang,shang,shang,shang,shao,shao,shao,shao,shao,shao,' +
        'shao,shao,shao,shao,shao,she,she,she,she,she,she,she,she,she,she,she,she,shen,shen,shen,sh' +
        'en,shen,shen,shen,shen,shen,shen,shen,shen,shen,shen,shen,shen,sheng,sheng,sheng,sheng,she' +
        'ng,sheng,sheng,sheng,sheng,sheng,sheng,shi,shi,shi,shi,shi,shi,shi,shi,shi,shi,shi,shi,she' +
        'n,shi,shi,shi,shi,shi,shi,shi,shi,shi,shi,shi,shi,shi,shi,shi,shi,shi,shi,shi,shi,shi,shi,' +
        'shi,shi,shi,shi,shi,shi,shi,shi,shi,shi,shi,shi,shou,shou,shou,shou,shou,shou,shou,shou,sh' +
        'ou,shou,shu,shu,shu,shu,shu,shu,shu,shu,shu,shu,shu,shu,shu,shu,shu,shu,shu,shu,shu,shu,sh' +
        'u,shu,shu,shu,shu,shu,shu,shu,shu,shu,shu,shu,shu,shua,shua,shuai,shuai,shuai,shuai,shuan,' +
        'shuan,shuang,shuang,shuang,shui,shui,shui,shui,shun,shun,shun,shun,shuo,shuo,shuo,shuo,si,' +
        'si,si,si,si,si,si,si,si,si,si,si,ci,shi,si,si,song,song,song,song,song,song,song,song,sou,' +
        'sou,sou,sou,su,su,su,su,su,su,su,su,su,su,su,su,suan,suan,suan,sui,sui,sui,sui,sui,sui,sui' +
        ',sui,sui,sui,sui,sun,sun,sun,suo,suo,suo,suo,suo,suo,suo,suo,ta,ta,ta,ta,ta,ta,ta,ta,ta,ta' +
        'i,tai,tai,tai,tai,tai,tai,tai,tai,tan,tan,tan,tan,tan,tan,tan,tan,tan,tan,tan,tan,tan,tan,' +
        'tan,tan,tan,tan,tang,tang,tang,tang,tang,tang,tang,tang,tang,tang,tang,tang,tang,tao,tao,t' +
        'ao,tao,tao,tao,tao,tao,tao,tao,tao,te,teng,teng,teng,teng,ti,ti,ti,ti,ti,ti,ti,ti,ti,ti,ti' +
        ',ti,ti,ti,ti,tian,tian,tian,tian,tian,tian,tian,tian,tiao,tiao,tiao,tiao,tiao,tie,tie,tie,' +
        'ting,ting,ting,ting,ting,ting,ting,ting,ting,ting,tong,tong,tong,tong,tong,tong,tong,tong,' +
        'tong,tong,tong,tong,tong,tou,tou,tou,tou,tu,tu,tu,tu,tu,tu,tu,tu,tu,tu,tu,tuan,tuan,tui,tu' +
        'i,tui,tui,tui,tui,tun,tun,tun,tuo,tuo,tuo,tuo,tuo,tuo,tuo,tuo,tuo,tuo,tuo,wa,wa,wa,wa,wa,w' +
        'a,wa,wai,wai,wan,wan,wan,wan,wan,wan,wan,wan,wan,wan,wan,wan,wan,wan,wan,wan,wan,wang,wang' +
        ',wang,wang,wang,wang,wang,wang,wang,wang,wei,wei,wei,wei,wei,wei,wei,wei,wei,wei,wei,wei,w' +
        'ei,wei,wei,wei,wei,wei,wei,wei,wei,wei,wei,wei,wei,wei,wei,wei,wei,wei,wei,wei,wei,wen,wen' +
        ',wen,wen,wen,wen,wen,wen,wen,wen,weng,weng,weng,wo,wo,wo,wo,wo,wo,wo,wo,wo,wu,wu,wu,wu,wu,' +
        'wu,wu,wu,wu,wu,wu,wu,wu,wu,wu,wu,wu,wu,wu,wu,wu,wu,wu,wu,wu,wu,wu,wu,wu,xi,xi,xi,xi,xi,xi,' +
        'xi,xi,xi,xi,xi,xi,xi,xi,xi,xi,xi,xi,xi,xi,xi,xi,xi,xi,xi,xi,xi,xi,xi,xi,xi,xi,xi,xi,xi,xia' +
        ',xia,xia,xia,xia,xia,xia,xia,xia,xia,sha,xia,xia,xian,xian,xian,xian,xian,xian,xian,xian,x' +
        'ian,xian,xian,xian,xian,xian,xian,xian,xian,xian,xian,xian,xian,xian,xian,xian,xian,xian,x' +
        'iang,xiang,xiang,xiang,xiang,xiang,xiang,xiang,xiang,xiang,xiang,xiang,xiang,xiang,xiang,x' +
        'iang,xiang,xiang,xiang,xiang,xiao,xiao,xiao,xue,xiao,xiao,xiao,xiao,xiao,xiao,xiao,xiao,xi' +
        'ao,xiao,xiao,xiao,xiao,xiao,xie,xie,xie,xie,xie,xie,xie,xie,xie,xie,xie,xie,xie,xie,xie,xi' +
        'e,xie,xie,xie,xie,xie,xin,xin,xin,xin,xin,xin,xin,xin,xin,xin,xing,xing,xing,xing,xing,xin' +
        'g,xing,xing,xing,xing,xing,xing,xing,xing,xing,xiong,xiong,xiong,xiong,xiong,xiong,xiong,x' +
        'iu,xiu,xiu,xiu,xiu,xiu,xiu,xiu,xiu,xu,xu,xu,xu,xu,xu,xu,xu,xu,xu,xu,xu,xu,chu,xu,xu,xu,xu,' +
        'xu,xuan,xuan,xuan,xuan,xuan,xuan,xuan,xuan,xuan,xuan,xue,xue,xue,xue,xue,xue,xun,xun,xun,x' +
        'un,xun,xun,xun,xun,xun,xun,xun,xun,xun,xun,ya,ya,ya,ya,ya,ya,ya,ya,ya,ya,ya,ya,ya,ya,ya,ya' +
        ',yan,yan,yan,yan,yan,yan,yan,yan,yan,yan,yan,yan,yan,yan,yan,yan,yan,yan,yan,yan,yan,yan,y' +
        'an,yan,yan,yan,yan,yan,yan,yan,yan,yan,yan,yang,yang,yang,yang,yang,yang,yang,yang,yang,ya' +
        'ng,yang,yang,yang,yang,yang,yang,yang,yao,yao,yao,yao,yao,yao,yao,yao,yao,yao,yao,yao,yao,' +
        'yao,yao,ye,ye,ye,ye,ye,ye,ye,ye,ye,ye,ye,ye,ye,ye,ye,yi,yi,yi,yi,yi,yi,yi,yi,yi,yi,yi,yi,y' +
        'i,yi,yi,yi,yi,yi,yi,yi,yi,yi,yi,yi,yi,yi,yi,yi,yi,yi,yi,yi,yi,yi,yi,yi,yi,yi,yi,yi,yi,yi,y' +
        'i,yi,yi,yi,yi,yi,yi,yi,yi,yi,yi,yin,yin,yin,yin,yin,yin,yin,yin,yin,yin,yin,yin,yin,yin,yi' +
        'n,yin,ying,ying,ying,ying,ying,ying,ying,ying,ying,ying,ying,ying,ying,ying,ying,ying,ying' +
        ',ying,yo,yong,yong,yong,yong,yong,yong,yong,yong,yong,yong,yong,yong,yong,yong,yong,you,yo' +
        'u,you,you,you,you,you,you,you,you,you,you,you,you,you,you,you,you,you,you,yu,yu,yu,yu,yu,y' +
        'u,yu,yu,yu,yu,yu,yu,yu,yu,yu,yu,yu,yu,yu,yu,yu,yu,yu,yu,yu,yu,yu,yu,yu,xu,yu,yu,yu,yu,yu,y' +
        'u,yu,yu,yu,yu,yu,yu,yu,yu,yu,yuan,yuan,yuan,yuan,yuan,yuan,yuan,yuan,yuan,yuan,yuan,yuan,y' +
        'uan,yuan,yuan,yuan,yuan,yuan,yuan,yuan,yue,yue,yue,yue,yao,yue,yue,yue,yue,yue,yun,yun,yun' +
        ',yun,yun,yun,yun,yun,yun,yun,yun,yun,za,za,za,zai,zai,zai,zai,zai,zai,zai,zan,zan,zan,zan,' +
        'zang,zang,zang,zao,zao,zao,zao,zao,zao,zao,zao,zao,zao,zao,zao,zao,zao,ze,ze,ze,ze,zei,zen' +
        ',zeng,zeng,ceng,zeng,zha,zha,zha,zha,ya,zha,zha,zha,zha,zha,za,zha,zha,zha,zhai,zhai,zhai,' +
        'zhai,zhai,zhai,zhan,zhan,zhan,zhan,zhan,zhan,zhan,nian,zhan,zhan,zhan,zhan,zhan,zhan,zhan,' +
        'zhan,zhan,zhang,zhang,zhang,zhang,zhang,zhang,zhang,zhang,zhang,zhang,zhang,zhang,zhang,zh' +
        'ang,zhang,zhao,zhao,zhao,zhao,zhao,zhao,zhao,zhao,zhao,zhao,zhe,zhe,zhe,zhe,zhe,zhe,zhe,zh' +
        'e,zhe,zhe,zhen,zhen,zhen,zhen,zhen,zhen,zhen,zhen,zhen,zhen,zhen,zhen,zhen,zhen,zhen,zhen,' +
        'zheng,zheng,zheng,zheng,zheng,zheng,zheng,zheng,zheng,zheng,zheng,zhen,zheng,zheng,zheng,z' +
        'hi,zhi,zhi,zhi,zhi,zhi,zhi,zhi,zhi,zhi,zhi,zhi,zhi,zhi,zhi,zhi,zhi,zhi,zhi,zhi,zhi,zhi,zhi' +
        ',zhi,zhi,zhi,zhi,zhi,zhi,zhi,zhi,zhi,zhi,zhi,zhi,zhi,zhi,zhi,zhi,zhi,zhi,zhi,zhi,zhong,zho' +
        'ng,zhong,zhong,zhong,zhong,zhong,zhong,zhong,zhong,zhong,zhou,zhou,zhou,zhou,zhou,zhou,zho' +
        'u,zhou,zhou,zhou,zhou,zhou,zhou,zhou,zhu,zhu,zhu,zhu,zhu,zhu,zhu,zhu,zhu,zhu,zhu,zhu,zhu,z' +
        'hu,zhu,zhu,zhu,zhu,zhu,zhu,zhu,zhu,zhu,zhu,zhu,zhu,zhua,zhao,zhuai,zhuan,zhuan,zhuan,zhuan' +
        ',zhuan,zhuan,zhuang,zhuang,zhuang,zhuang,zhuang,zhuang,zhuang,chui,zhui,zhui,zhui,zhui,zhu' +
        'i,zhun,zhun,zhuo,zhuo,zhuo,zhuo,zuo,zhuo,zhuo,zhuo,zhe,zhuo,zhuo,zi,zi,zi,zi,zi,zi,zi,zi,z' +
        'ai,zi,zi,zi,zi,zi,zi,zong,zong,zong,zong,zong,zong,zong,zou,zou,zou,zou,zu,zu,zu,zu,zu,zu,' +
        'zu,zu,zuan,zuan,zui,zui,zui,zui,zun,zun,zuo,zuo,zuo,zha,zuo,zuo,zuo,zuo,,,,,'
    ),
    (
        'chu,ji,wu,gai,nian,sa,pi,gen,cheng,ge,nao,e,gun,yu,pie,bi,tuo,yao,yao,zhi,di,xin,yin,kui,y' +
        'u,gao,tao,zhu,ji,nai,mie,ji,qi,mi,bei,se,gu,ze,she,cuo,yan,jue,si,ye,yan,fang,po,gui,kui,b' +
        'ian,ze,gua,you,dao,yi,wen,jing,ku,gui,kai,la,ji,shan,wan,kuai,piao,jue,qiao,huo,yi,jiong,w' +
        'ang,ren,ding,zhang,le,sa,ge,mu,ren,yu,pi,ya,wa,wu,chang,cang,kang,zhu,ning,ka,you,yi,gou,t' +
        'ong,tuo,ni,ga,ji,er,you,kua,kan,zhu,yi,tiao,chai,jiao,nong,mou,chou,yan,li,qiu,li,yu,ping,' +
        'yong,qi,feng,qian,ruo,pai,zhuo,shu,luo,wo,bi,ti,guan,kong,ju,fen,yan,xie,ji,wei,zong,lou,t' +
        'ang,bin,nuo,chi,xi,jing,jian,jiao,jiu,tong,xuan,dan,tong,tun,she,qian,zu,yue,cuan,di,xi,xu' +
        'n,hong,guo,chan,kui,bao,pu,hong,fu,fu,su,si,tou,yan,bo,gun,mao,xie,luan,pou,bing,ying,luo,' +
        'lei,bing,hu,lie,xian,song,mi,zhong,ming,yan,jie,hong,shan,ou,ju,ne,gu,he,di,zhao,qu,yi,kua' +
        'ng,lei,gua,ji,hui,shen,gou,quan,zheng,hun,xu,qiao,gao,kuang,ei,zou,zhuo,wei,yu,shen,chan,s' +
        'ui,chen,jian,xue,ye,e,yu,xuan,an,di,zi,pian,mo,dang,su,shi,mi,zhe,jian,zen,qiao,jue,yan,zh' +
        'an,chen,jie,jin,fu,wu,qian,jing,ban,dian,zuo,bei,xing,gai,zhi,nie,zou,chui,pi,wei,huang,ku' +
        'i,xi,han,qiong,kuang,mang,wu,fang,bing,pi,bei,ye,di,tai,jia,zhi,zhu,kuai,qie,huan,yun,li,y' +
        'ing,gao,xi,fu,pi,tan,yan,juan,yan,yin,zhang,po,shan,zou,ling,feng,chu,huan,mai,qu,shao,he,' +
        'ge,meng,xu,xie,sou,xie,jue,yin,qian,dang,chang,si,bian,ben,qiu,ben,e,fa,shu,ji,yong,he,wei' +
        ',wu,ge,zhen,kuang,pi,yi,li,qi,ban,gan,long,dian,lu,che,chi,tuo,ni,mu,ao,ya,die,dong,kai,sh' +
        'an,shang,nao,gai,yin,cheng,shi,guo,xun,lie,yuan,zhi,an,yi,pi,nian,peng,tu,sao,dai,ku,die,y' +
        'in,leng,hou,ge,yuan,man,yong,liang,chi,xin,pi,yi,cao,jiao,nai,du,qian,ji,wan,qiong,qi,xian' +
        'g,fu,yan,yun,fei,ji,li,e,ju,pi,zhi,rui,xian,chang,cong,qin,wu,qian,qi,shan,bian,zhu,kou,yi' +
        ',mo,gan,pie,long,ba,mu,ju,ran,qing,chi,fu,ling,niao,yin,mao,ying,qiong,min,shao,qian,ti,ra' +
        'o,bi,ci,ju,tong,hui,zhu,ting,qiao,fu,ren,xing,quan,hui,xun,ming,ji,jiao,chong,jiang,luo,xi' +
        'ng,xun,gen,jin,mai,sun,hong,zhou,kan,bi,shi,wo,you,e,mei,you,li,tu,xian,fu,sui,you,di,shen' +
        ',guan,lang,ying,chun,jing,qi,xi,song,jin,nai,qi,ba,shu,chang,tie,yu,huan,bi,fu,tu,dan,cui,' +
        'yan,ju,dang,jian,wan,ying,gu,han,qia,feng,ren,xiang,wei,chan,kai,qi,kui,xi,e,bao,pa,ting,l' +
        'ou,pai,xuan,jia,zhen,shi,ru,mo,en,bei,weng,hao,ji,li,bang,jian,shuo,lang,ying,yu,su,meng,d' +
        'ou,xi,lian,cu,lin,qu,kou,xu,liao,hui,xun,jue,rui,zui,ji,meng,fan,qi,hong,xie,hong,wei,yi,w' +
        'eng,sou,bi,hao,tai,ru,xun,xian,gao,li,huo,qu,heng,fan,nie,mi,gong,yi,kuang,lian,da,yi,xi,z' +
        'ang,pao,you,liao,ga,gan,shou,men,tuan,chen,fu,pan,ao,jie,jiao,za,yi,lv,jun,tian,ye,ai,na,j' +
        'i,guai,bai,ju,pou,lie,qian,guan,die,zha,ya,qin,yu,an,xuan,bing,kui,yuan,shu,en,chuai,jian,' +
        'shuo,zhan,nuo,sang,luo,ying,zhi,han,zhe,xie,lu,zun,cuan,gan,huan,pi,xing,zhuo,huo,zuan,nan' +
        'g,yi,te,dai,shi,bu,chi,ji,kou,dao,le,zha,ya,yao,fu,wu,yi,dai,li,e,bi,bei,guo,qin,yin,za,ka' +
        ',ga,gu,ling,dong,ning,duo,nao,you,si,kuang,ji,shen,hui,da,lie,yi,xiao,bi,ci,guang,hui,xiu,' +
        'yi,pai,kuai,duo,ji,mie,mi,zha,nong,gen,mou,ma,chi,lao,geng,wu,zha,suo,zao,xi,zuo,ji,feng,z' +
        'e,nuo,miao,lin,zhuan,zhao,tao,hu,cui,sha,yo,dan,bo,ding,lang,li,shua,chuai,die,da,nan,li,k' +
        'ui,jie,yong,kui,jiu,sou,yin,chi,jie,lou,ku,o,hui,qin,ao,su,du,ke,nie,he,chen,suo,ge,a,n,ha' +
        'o,die,ai,ai,suo,hai,tong,chi,pei,lei,cao,piao,qi,ying,beng,sou,di,mi,peng,jue,liao,pu,chua' +
        'i,jiao,o,qin,lu,ceng,deng,hao,jin,jue,yi,sai,pi,ru,ca,huo,nang,wei,jian,nan,lun,hu,ling,yo' +
        'u,yu,qing,yu,huan,wei,zhi,pei,tang,chou,ze,guo,wei,wo,man,zhang,fu,fan,ji,qi,qian,qi,qu,ya' +
        ',xian,ao,cen,lan,ba,hu,ke,dong,jia,xiu,dai,gou,mao,min,yi,dong,jiao,xun,zheng,lao,lai,song' +
        ',yan,gu,xiao,guo,kong,jue,rong,yao,wai,zai,wei,yu,cuo,lou,zi,mei,sheng,song,ji,zhang,lin,d' +
        'eng,bin,yi,dian,chi,pang,cu,xun,yang,hou,lai,xi,chang,huang,yao,zheng,jiao,qu,shan,quan,qi' +
        'u,an,guang,ma,niu,yun,xia,pao,fei,rong,kuai,shou,sun,bi,juan,li,yu,xian,yin,suan,yi,guo,lu' +
        'o,ni,she,cu,mi,hu,cha,wei,wei,mei,nao,zhang,jing,jue,liao,xie,xun,huan,chuan,huo,sun,yin,z' +
        'hi,shi,tang,tun,xi,ren,yu,chi,yi,xiang,bo,yu,hun,cha,sou,mo,xiu,jin,san,zhuan,nang,pi,wu,g' +
        'ui,pao,xiu,xiang,tuo,an,yu,bi,geng,ao,jin,chan,xie,lin,ying,xin,dao,cun,chan,wu,zhi,ou,cho' +
        'ng,wu,kai,chang,chuang,song,bian,niu,hu,chu,peng,da,yang,zuo,ni,fu,chao,yi,yi,tong,yan,ce,' +
        'kai,xun,ke,yun,bei,song,qian,kui,kun,yi,ti,quan,qie,xing,fei,chang,wang,chou,hu,cui,yun,ku' +
        'i,e,leng,zhui,qiao,bi,su,qian,yong,jing,qiao,chong,chu,lin,meng,tian,hui,shuan,yan,wei,hon' +
        'g,min,kang,ta,lv,kun,jiu,lang,yu,chang,xi,wen,hun,e,qu,que,he,tian,que,han,qiang,pan,qiang' +
        ',shui,qi,si,cha,feng,yuan,mu,mian,dun,mi,gu,bian,wen,hang,wei,le,gan,shu,long,lu,yang,si,t' +
        'uo,ling,mao,luo,xuan,pan,tuo,hong,min,jing,huan,wei,lie,jia,zhen,yin,hui,zhu,ji,xu,hui,tao' +
        ',xun,jiang,liu,hu,xun,ru,su,wu,lai,wei,zhuo,juan,cen,bang,xi,mei,huan,zhu,qi,xi,song,du,zh' +
        'uo,pi,mian,gan,fei,cong,shen,guan,lu,shuan,xie,yan,mian,jiao,sou,huang,xu,pen,jian,xuan,wo' +
        ',mei,yan,qin,ke,she,mang,ying,pu,li,ru,ta,hun,bi,xiu,fu,tang,pang,ming,huang,ying,xiao,lan' +
        ',cao,hu,luo,huan,lian,zhu,yi,lu,xuan,gan,shu,si,shan,shao,tong,chan,lai,sui,li,dan,chan,li' +
        'an,ru,pu,bi,hao,zhuo,han,xie,ying,yue,fen,hao,ba,mian,gui,dang,mi,you,chen,ning,qian,qian,' +
        'wu,liao,qian,huan,jian,jian,chuo,ya,wu,jiong,ze,yi,er,jia,jing,dai,hou,pang,bu,li,qiu,xiao' +
        ',ti,qun,kui,wei,huan,lu,chuan,huang,qiu,xia,ao,gou,ta,liu,xian,lin,ju,xie,miao,sui,la,ji,h' +
        'ui,tuan,zhi,kao,zhi,ji,e,can,xi,ju,chan,jing,nu,mi,fu,bi,yu,che,shuo,fei,yan,wu,yu,bi,jin,' +
        'zi,gui,niu,yu,si,da,zhou,shan,qie,ya,rao,shu,luan,jiao,pin,cha,li,ping,wa,xian,suo,di,wei,' +
        'e,jing,biao,jie,chang,bi,chan,nu,ao,yuan,ting,wu,gou,mo,pi,ai,pin,chi,li,yan,qiang,piao,ch' +
        'ang,lei,zhang,xi,shan,bi,niao,ma,shuang,ga,ga,fu,nu,zi,jie,jue,bao,zang,si,fu,zou,yi,nu,da' +
        'i,xiao,hua,pian,li,qi,ke,zhui,can,zhi,wu,ao,liu,shan,biao,cong,chan,ji,xiang,si,yu,zhou,ge' +
        ',wan,kuang,yun,pi,shu,gan,xie,fu,zhou,fu,chu,dai,ku,hang,jiang,geng,xiao,ti,ling,qi,fei,sh' +
        'ang,gun,duo,shou,liu,quan,wan,zi,ke,xiang,ti,miao,hui,si,bian,gou,zhui,min,jin,zhen,ru,gao' +
        ',li,yi,jian,bin,piao,man,lei,mou,sao,xie,liao,zeng,jiang,qian,qiao,huan,zuan,yao,ji,chuan,' +
        'zai,yong,ding,ji,wei,bin,wen,jue,ke,long,dian,dai,po,min,jia,er,gong,xu,ya,hang,yao,luo,xi' +
        ',hui,lian,qi,ying,qi,hu,kun,yan,cong,wan,chen,ju,mao,yu,yuan,xia,nao,ai,tang,jin,huang,yin' +
        'g,cui,cong,xuan,zhang,pu,can,qu,lu,bi,zan,wen,wei,yun,tao,wu,biao,qi,cha,ma,li,pi,miao,yao' +
        ',rui,jian,chu,cheng,cong,xiao,fang,pa,zhu,nai,zhi,zhe,long,jiu,ping,lu,xia,xiao,you,zhi,tu' +
        'o,zhi,ling,gou,di,li,duo,cheng,kao,lao,ya,rao,zhi,zhen,guang,qi,ting,gua,jiu,hua,heng,gui,' +
        'jie,luan,juan,an,xu,fan,gu,fu,jue,zi,suo,ling,chu,fen,du,qian,zhao,luo,chui,liang,guo,jian' +
        ',di,ju,zou,shen,nan,zha,lian,lan,ji,pin,ju,qiu,duan,chui,chen,lv,cha,ju,xuan,mei,ying,zhen' +
        ',fei,ta,sun,xie,gao,cui,gao,shuo,bin,rong,zhu,xie,jin,qiang,qi,chu,tang,zhu,hu,gan,yue,qin' +
        'g,tuo,jue,qiao,qin,lu,zun,xi,ju,yuan,lei,yan,lin,bo,cha,you,ao,mo,cu,shang,tian,yun,lian,p' +
        'iao,dan,ji,bin,yi,ren,e,gu,ke,lu,zhi,yi,zhen,hu,li,yao,shi,zhi,quan,lu,zhe,nian,wang,chuo,' +
        'zi,cou,lu,lin,wei,jian,qiang,jia,ji,ji,kan,deng,gai,jian,zang,ou,ling,bu,beng,zeng,pi,pu,g' +
        'a,la,gan,hao,tan,gao,ze,xin,yun,jiong,he,zan,mao,yu,chang,ni,qi,cheng,ye,chao,yan,hui,bu,h' +
        'an,gui,xuan,kui,ai,ming,tun,xun,yao,xi,nang,ben,shi,kuang,yi,zhi,zi,gai,jin,zhen,lai,qiu,j' +
        'i,dan,fu,chan,ji,xi,di,yu,gou,jin,qu,jian,jiang,pin,mao,gu,wu,gu,ji,ju,jian,pian,kao,qie,s' +
        'a,bai,ge,bai,mao,mu,cui,jian,san,shu,chang,lu,pu,qu,pie,dao,xian,chuan,dong,ya,yin,ke,yun,' +
        'pu,chi,jiao,du,die,you,yuan,guo,yue,wo,rong,huang,jing,ruan,tai,gong,zhun,na,yao,qian,long' +
        ',dong,ka,lu,jia,shen,zhou,zuo,gua,zhen,qu,zhi,jing,guang,dong,yan,kuai,sa,hai,pian,zhen,mi' +
        ',tun,luo,cuo,pao,wan,niao,jing,yan,fei,yu,zong,ding,jian,cou,nan,mian,wa,e,shu,cheng,ying,' +
        'ge,lv,bin,teng,zhi,chuai,gu,meng,sao,shan,lian,lin,yu,xi,yi,sha,xin,she,biao,sa,ju,sou,bia' +
        'o,biao,shu,gou,gu,hu,fei,ji,lan,yu,pei,mao,zhan,jing,ni,liu,yi,yang,wei,dun,qiang,shi,hu,z' +
        'hu,xuan,tai,ye,yang,wu,han,men,chao,yan,hu,yu,wei,duan,bao,xuan,bian,tui,liu,man,shang,yun' +
        ',yi,yu,fan,sui,xian,jue,cuan,biao,dao,xu,xi,li,hu,jiong,hu,fei,shi,si,xian,zhi,qu,hu,fu,zu' +
        'o,mi,zhi,ci,zhen,tiao,qi,chan,xi,zhuo,xi,rang,te,tan,dui,jia,hui,nv,nen,yang,zi,que,qian,m' +
        'in,te,qi,dui,mao,men,gang,yu,yu,da,xue,miao,ji,gan,dang,huo,che,dun,ya,zhuo,bian,feng,fa,a' +
        'i,li,long,zha,tong,di,la,tuo,fu,xing,mang,xia,qiao,zhai,dong,nao,ge,wo,qi,dui,bei,ding,che' +
        'n,du,jie,di,xuan,bian,zhe,gun,sang,qing,qu,dun,deng,jiang,ca,meng,bo,kan,zhi,fu,fu,xu,mian' +
        ',kou,dun,miao,dan,sheng,yuan,yi,sui,zi,chi,mou,lai,jian,di,suo,ya,ni,sui,pi,rui,sou,kui,ma' +
        'o,ke,ming,piao,cheng,kan,lin,gu,ting,bi,quan,tian,fan,zhen,she,wan,tuan,fu,gang,gu,li,yan,' +
        'pi,lan,li,ji,zeng,he,guan,juan,jin,ga,yi,po,zhao,liao,tu,chuan,shan,men,chai,nv,bu,tai,ju,' +
        'ban,qian,fang,kang,tou,huo,ba,yu,zheng,gu,ke,po,bu,bo,yue,mu,tan,dian,shuo,shi,xuan,ta,bi,' +
        'ni,pi,duo,kao,lao,er,you,cheng,jia,nao,ye,dang,diao,yin,kai,zhu,ding,diu,hua,quan,ha,sha,d' +
        'iao,zheng,se,chong,tang,an,ru,lao,lai,te,keng,zeng,li,gao,e,cuo,lve,liu,kai,jian,lang,qin,' +
        'ju,a,qiang,nuo,ben,de,ke,kun,gu,huo,pei,juan,tan,zi,qie,kai,si,e,cha,sou,huan,ai,lou,qiang' +
        ',fei,mei,mo,ge,juan,na,liu,yi,jia,bin,biao,tang,man,luo,yong,zu,xuan,di,chan,jue,pu,lu,dui' +
        ',lan,pu,cuan,qiang,deng,huo,zhuo,yi,cha,biao,zhong,shen,cuo,zhi,bi,zi,mo,shu,lv,ji,fu,lang' +
        ',ke,ren,zhen,ji,se,nian,fu,rang,gui,jiao,hao,xi,po,die,hu,yong,jiu,yuan,bao,zhen,gu,dong,l' +
        'u,qu,chi,si,er,zhi,gua,xiu,luan,bo,li,gu,yu,xian,ti,wu,miao,an,bei,chun,hu,e,ci,mei,wu,yao' +
        ',jian,ying,zhe,liu,liao,jiao,jiu,yu,hu,lu,guan,ne,ding,jie,li,shan,li,you,gan,ke,dan,zha,p' +
        'ao,zhu,xuan,jia,ya,yi,zhi,lao,wu,cuo,xian,sha,zhu,fei,gu,wei,yu,yu,dan,la,yi,hou,chai,lou,' +
        'jia,sao,chi,mo,ban,ji,huang,biao,luo,ying,zhai,long,yin,chou,ban,lai,yi,dian,pi,dian,qu,yi' +
        ',song,xi,qiong,zhun,bian,yao,tiao,dou,ke,yu,xun,ju,yu,yi,cha,na,ren,jin,mei,pan,dang,jia,g' +
        'e,ken,lian,cheng,lian,jian,biao,chu,ti,bi,ju,duo,da,bei,bao,lv,bian,lan,chi,zhe,qiang,ru,p' +
        'an,pi,xu,jun,cun,jin,lei,zi,chao,si,huo,lao,tang,ou,lou,jiang,nou,mo,die,ding,dan,ling,nin' +
        'g,gua,kui,ao,tan,han,qi,hang,jie,he,ying,ke,han,e,zhuan,nie,man,sang,hao,ru,pin,hu,qian,qi' +
        'u,ji,chai,hui,ge,meng,fu,pi,rui,xian,hao,jie,gong,dou,yin,chi,han,gu,he,li,you,ran,zha,qiu' +
        ',ling,cheng,you,qiong,jia,nao,zhi,si,qu,ting,kuo,qi,jiao,yang,mou,shen,zhe,shao,wu,li,chu,' +
        'fu,qiang,qing,qi,xi,yu,fei,guo,guo,yi,pi,tiao,quan,wan,lang,meng,chun,rong,nan,fu,kui,ke,f' +
        'u,sou,yu,you,lou,qiu,bian,mao,qin,ao,man,mang,ma,yuan,xi,chi,tang,pang,shi,huang,cao,piao,' +
        'tang,xi,xiang,zhong,zhang,shuai,mao,peng,hui,pan,shan,huo,meng,chan,lian,mie,li,du,qu,fou,' +
        'ying,qing,xia,shi,zhu,yu,ji,du,ji,jian,zhao,zi,hu,qiong,po,da,sheng,ze,gou,li,si,tiao,jia,' +
        'bian,chi,kou,bi,xian,yan,quan,zheng,yun,shi,gang,pa,shao,xiao,qing,ze,qie,zhu,ruo,qian,tuo' +
        ',bi,dan,kong,yuan,xiao,zhen,kui,huang,hou,gou,fei,li,bi,chi,su,mie,dou,lu,duan,gui,dian,za' +
        'n,deng,bo,lai,zhou,yu,yu,chong,xi,nie,nv,chuan,shan,yi,bi,zhong,ban,fang,ge,lu,zhu,ze,xi,s' +
        'hao,wei,meng,shou,cao,chong,meng,qin,niao,jia,qiu,sha,bi,di,qiang,suo,jie,tang,xi,xian,mi,' +
        'ba,li,tiao,xi,zi,can,lin,zong,san,hou,zan,ci,xu,rou,qiu,jiang,gen,ji,yi,ling,xi,zhu,fei,ji' +
        'an,pian,he,yi,mi,zhi,qi,qi,yao,dao,fu,qu,jiu,ju,lie,zi,zan,nan,zhe,jiang,shi,ding,gan,zhou' +
        ',yi,gu,cu,tuo,xian,ming,zhi,yan,shai,cheng,tu,lei,kun,pei,hu,ti,xu,hai,tang,lao,bu,jiao,xi' +
        ',ju,li,xun,shi,cuo,dun,qiong,xue,cu,bie,bao,ta,jian,fu,qiang,zhi,fu,shan,li,tuo,jia,bo,tai' +
        ',kui,qiao,bi,xian,xian,ji,jiao,liang,ji,chuo,huai,chi,zhi,dian,bo,zhi,jian,die,chuai,zhong' +
        ',ju,duo,cuo,pian,rou,nie,pan,qi,chu,jue,pu,fan,cu,zhu,lin,chan,lie,zuan,xie,zhi,diao,mo,xi' +
        'u,mo,pi,hu,jue,shang,gu,zi,gong,su,zhi,zi,qing,jing,yu,li,wen,ting,ji,pei,fei,sha,yin,ai,x' +
        'ian,mai,chen,ju,bao,tiao,zi,ken,yu,chuo,wo,min,yuan,tuo,zhui,sun,juan,ju,luo,qu,chou,qiong' +
        ',luan,wu,zan,mou,ao,liu,bei,xin,you,fang,ba,ping,nian,lu,su,fu,hou,tai,gui,jie,wei,er,ji,j' +
        'iao,xiang,xun,geng,li,lian,jian,shi,tiao,gun,sha,huan,ji,qing,ling,zou,fei,kun,chang,gu,ni' +
        ',nian,diao,shi,zi,fen,die,e,qiu,fu,huang,bian,sao,ao,qi,ta,guan,yao,le,biao,xue,man,min,yo' +
        'ng,gui,shan,zun,li,da,yang,da,qiao,man,jian,ju,rou,gou,bei,jie,tou,ku,gu,di,hou,ge,ke,bi,l' +
        'ou,qia,kuan,bin,du,mei,ba,yan,liang,xiao,wang,chi,xiang,yan,tie,tao,yong,biao,kun,mao,ran,' +
        'tiao,ji,zi,xiu,quan,jiu,bin,huan,lie,mo,hui,mi,ji,jun,zhu,mi,qi,ao,she,lin,dai,chu,you,xia' +
        ',yi,qu,du,li,qing,can,an,fen,you,wu,yan,xi,qiu,han,zha'
    ),
)

def _decoder(chunks, hi_from, hi_to):
    """按码位反解字符并配平音节：表里只存读音，字符由 GB2312 码位自己算出来。"""
    text = "".join(chunks)
    cells = text.split(",")
    out = {}
    n = 0
    for hi in range(hi_from, hi_to + 1):
        for lo in range(0xA1, 0xFF):
            if n >= len(cells):
                return out
            seg = cells[n]
            n += 1
            if not seg:
                continue
            try:
                out[bytes((hi, lo)).decode("gb2312")] = seg
            except UnicodeDecodeError:
                pass
    return out


# 表按码位分两段：一级字 0xB0A1–0xD7F9（3755 个，恰好排满），二级字 0xD8A1–0xF7FE
# （3008 个）。段内**逐槽**存，空槽是 GB2312 里没用到的位置（一级尾部有三个），
# 用空串占位而不是删掉——删掉会让后面所有字的码位偏移一格，那种错不会报错，
# 只会让整张表从某一位起全部对错字（本模块最坏的一类静默失效）。
_L1 = _decoder(_ROWS[0], 0xB0, 0xD7)
_L2 = _decoder(_ROWS[1], 0xD8, 0xF7)
_BY_CHAR = {}
_BY_CHAR.update(_L1)
_BY_CHAR.update(_L2)
# 音节全集：由表本身反推，不另列一份清单（另列就会与表分家）。
_SYLLABLE_SET = frozenset(v for v in _BY_CHAR.values() if v)


def syllable(ch):
    """单个汉字 → 无声调拼音音节；不是 GB2312 里的汉字返回空串（调用方按拼不出处理）。"""
    return _BY_CHAR.get(str(ch or ""), "")


def is_han(ch):
    return bool(syllable(ch))


def lazy_pinyin(text):
    """把一串文字转成音节列表：汉字→音节，ASCII 字母数字→小写原样，其余（含繁体、
    生僻扩展字、标点）→ **原样保留成一个非音节段**，由调用方判断这段能不能进候选域名。

    为什么不悄悄丢掉读不出来的字：丢掉会把「上海A镠科技」拼成 `shanghaiakexin`，
    一个看着像那么回事、实际根本不存在的域名——比返回一个明显的异常形状更坏。
    调用方用 :func:`is_syllable` 就能分辨哪些段是真音节。
    """
    out = []
    for ch in str(text or ""):
        s = _BY_CHAR.get(ch)
        if s:
            out.append(s)
        elif ch.isascii() and ch.isalnum():
            out.append(ch.lower())
        else:
            out.append("")              # 读不出来：留空槽而不是丢字（长度即账）
    return out


def is_syllable(seg):
    """这一段是不是**表里真有的**拼音音节（名字即判据，不是「长得像字母就行」）。

    为什么必须按表校：上一版写的是「非空且全小写字母数字」，那是「安全 ASCII 段」判定，
    不是音节判定。`lazy_pinyin("Krones AG")` 会把拉丁字母逐字原样返回，于是守卫把
    ['k','r','o',...] 全当成合法读音，拼出 `kronesag.cn` 这种纯字母序假域名——守卫恰好
    在本该拦住它的地方放了行（离线套件实测抓到）。
    """
    return str(seg or "") in _SYLLABLE_SET
