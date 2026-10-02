import { api } from "../../api/client";
import type { CommunityBook } from "../data/mockBook";
export const communityCatalog: CommunityBook[]=[];
export async function loadCommunityCatalog(query=""){
 const result=await api.community("book",query,0);
 communityCatalog.splice(0,communityCatalog.length,...result.items.map(post=>({id:post.id,title:post.title,catalogTitle:post.book.title,owner:post.author,cover:post.book.cover_url??"",subject:"其他" as const,grade:"" as const,version:"",learners:post.downloads,progress:0,flashcardCount:post.content.cards?.length??0,recommended:true,tags:[post.kind],description:post.description,chapters:[]})));
 return communityCatalog;
}
