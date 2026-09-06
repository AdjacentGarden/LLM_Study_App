import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type { UserProfile } from "../types/api";

export const DEFAULT_AVATAR="/assets/brand/profile-avatar-student-v2.png";
export function UserProfilePage({onSaved,onBack,onBusy}:{onSaved:(value:UserProfile)=>void;onBack:()=>void;onBusy:(value:boolean)=>void}) {
  const [saved,setSaved]=useState<UserProfile|null>(null),[nickname,setNickname]=useState(""),[age,setAge]=useState(""),[bio,setBio]=useState("");
  const [image,setImage]=useState<string|null>(null),[reset,setReset]=useState(false),[loading,setLoading]=useState(true),[busy,setBusy]=useState(false),[reading,setReading]=useState(false),[error,setError]=useState("");
  const reader=useRef<FileReader|null>(null),[reload,setReload]=useState(0);
  useEffect(()=>{let cancelled=false;setLoading(true);setError("");void api.userProfile().then(value=>{if(cancelled)return;setSaved(value);setNickname(value.nickname);setAge(value.age===null?"":String(value.age));setBio(value.bio);setImage(null);setReset(false);}).catch(e=>{if(!cancelled)setError(e.message);}).finally(()=>{if(!cancelled)setLoading(false);});return()=>{cancelled=true;reader.current?.abort();};},[reload]);
  function choose(file?:File){if(!file)return;setError("");if(!["image/jpeg","image/png","image/webp"].includes(file.type)||file.size>2*1024*1024){setError("请选择不超过 2 MB 的 JPG、PNG 或 WebP 图片。");return;}reader.current?.abort();const next=new FileReader();reader.current=next;setReading(true);next.onload=()=>{setImage(String(next.result));setReset(false);setReading(false);};next.onerror=()=>{setError("图片读取失败，请重新选择。");setReading(false);};next.readAsDataURL(file);}
  const validAge=age===""||(/^\d+$/.test(age)&&Number(age)>=1&&Number(age)<=120);
  async function save(){if(!saved||!validAge)return;setBusy(true);onBusy(true);setError("");try{const result=await api.saveUserProfile({nickname:nickname.trim(),age:age===""?null:Number(age),bio:bio.trim(),revision:saved.revision,avatar_data_url:image??undefined,reset_avatar:reset});onSaved(result);}catch(e){setError((e as Error).message);}finally{setBusy(false);onBusy(false);}}
  if(loading)return <p role="status" className="profile-loading">正在读取个人资料…</p>;
  return <form className="user-profile-page" onSubmit={event=>{event.preventDefault();void save();}}>
    <section className="user-avatar-editor"><span className="kicker">让学习空间，更像你</span><img src={image??(!reset&&saved?.avatar_url?saved.avatar_url:DEFAULT_AVATAR)} alt="个人头像预览"/><label className={`avatar-upload ${busy||reading?"disabled":""}`}>更换头像<input aria-label="上传头像" type="file" accept="image/jpeg,image/png,image/webp" disabled={busy||reading||!saved} onChange={e=>{choose(e.target.files?.[0]);e.target.value="";}}/></label><button type="button" className="avatar-reset" disabled={busy||reading||!saved} onClick={()=>{setImage(null);setReset(true);}}>恢复默认头像</button></section>
    <section className="user-details-form"><label>昵称<input aria-label="昵称" autoComplete="nickname" maxLength={32} required disabled={busy||!saved} value={nickname} onChange={e=>setNickname(e.target.value)} placeholder="大家怎么称呼你？"/></label><label>年龄 <small>选填，不会在社区公开</small><input aria-label="年龄" type="number" inputMode="numeric" min={1} max={120} step={1} disabled={busy||!saved} value={age} onChange={e=>setAge(e.target.value)} placeholder="暂不填写"/></label>{!validAge&&<p className="community-error">年龄请输入 1–120 的整数，或留空。</p>}<label>个人简介 <small>{bio.length}/160</small><textarea aria-label="个人简介" rows={3} maxLength={160} disabled={busy||!saved} value={bio} onChange={e=>setBio(e.target.value.replace(/[\r\n]/g," "))} placeholder="写下喜欢的学科或学习愿望…"/></label></section>
    {error&&<div className="community-error" role="alert"><p>{error}</p><button type="button" disabled={busy} onClick={()=>setReload(r=>r+1)}>重新载入资料（放弃未保存修改）</button></div>}
    <button className="primary" disabled={busy||reading||!saved||!nickname.trim()||!validAge}>{busy?"正在保存…":reading?"正在读取图片…":"保存个人资料"}</button><button type="button" className="secondary" disabled={busy} onClick={onBack}>取消修改</button>
  </form>;
}
