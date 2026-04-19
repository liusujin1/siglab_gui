  function [sel_t,dispvec_t,sel_s,dispvec_s,sel_x,dispvec_x]=dispchns(hax,hpuvec,clist,visel)
% function [sel_t,dispvec_t,sel_s,dispvec_s,sel_x,dispvec_x]=dispchns(hax,hpuvec,clist,visel)
% sel_t,s     =  channels currently being displayed
% dispvec_t,s =  index into data vectors 
% hax         = handle of upper dual axis
% hpuvec      = handles of display popups
% clist       = list of enabled channels 
% Dick Benson, DSP Technology (for multi box support)
     if strcmp(visel,'vos')
         if strcmp(get(hax,'visible'),'on')  % dual plots
               ib=1; ie=4;
         else                                % single plot
               ib=5; ie=6;
         end;
         puofs = 1;    % vos popups have time as 1st entry...
         
         sel_t=[];
         for i=ib:ie
             ptr =get(hpuvec(i),'value')-puofs; 
             if ptr >0
                if  isempty(sel_t)
                    sel_t=clist(ptr); 
                elseif  isempty(find(sel_t==clist(ptr)))
                    sel_t=[sel_t,clist(ptr)];
                end;    
             end;
         end;
         sel_t=sort(sel_t);
         dispvec_t=[];
         for i=1:length(sel_t)
             dispvec_t =[dispvec_t,find(clist==sel_t(i))]; 
         end;
     elseif strcmp(visel,'vsa')
         % puofs = 0; vsa popups have channel as 1st entry... no orbit stuff (tG)
         sel_t     =clist(get(hpuvec(1),'value'));
         dispvec_t =find(clist==sel_t); 
         if strcmp(get(hax,'visible'),'on')  % dual plots  time / spectrum
             sel_s=clist(get(hpuvec(2),'value'));
             dispvec_s =find(clist==sel_s);   
         else                                % single plot spectrum only
             sel_s     =clist(get(hpuvec(3),'value')); 
             dispvec_s =find(clist==sel_s);
         end;
     elseif strcmp(visel,'vna')   
             sel_t     =clist(get(hpuvec(1),'value'));
             dispvec_t =find(clist==sel_t); 
             sel_s=clist(get(hpuvec(2),'value'));
             dispvec_s =find(clist==sel_s); 
             % single plot for xfer function
             sel_x     = clist(get(hpuvec(3),'value')+1); 
             dispvec_x = get(hpuvec(3),'value');  % no Fvec 
     else
         disp('dispchns.m tuned for vos,vsa,vna')
     end;
     
% end function
















