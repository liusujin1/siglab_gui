  function [data,ovld,gotit,seqnum]=getdata(Req_id,MaxTime,OldData,ErrorMsg)
% function [data,ovld,gotit,seqnum]=getdata(Req_id,MaxTime,OldData,ErrorMsg)
% 12-Feb-1997 22:09 GLS - don't try to get invalid data
% 3-Feb-1997 GLS - handle ovld and seqnum args when no data available
% Try DataGet for no longer than MaxTime
% If time is exceeded, return OldData and produce (optional) ErrorMsg 
% in command window.
% Dick Benson DSP Technology
   tic;
   gotit=1;
   Rdy = 0;
   if (Req_id < 0)
      gotit = -1;
   end;
   while Rdy == 0 & gotit ==1
     Rdy = siglab('DataRdy',Req_id);
     if toc >= MaxTime 
        gotit=0; 
     else
        drawnow;  % mandatory for SigLab v2.25 under v5.2 and  beyond 5/1/98
     end;
   end;
   if Rdy < 0
      gotit = -2;
   end;
   if gotit == 1
      [data,ovld,seqnum]=siglab('DataGet',Req_id);
   else
      seqnum = 666;
      ovld = 0;
      if Req_id >= 0
         % siglab('debug',-1)
         % Req_id
         siglab('DataAbort',Req_id);
      end;
      data=OldData;
      if nargin ==4
         if gotit == 0,
            disp(['Time Out:',ErrorMsg]);
         else
            disp(['Invalid request:',ErrorMsg]);
         end;
      end;   
      
   end;
% end function
