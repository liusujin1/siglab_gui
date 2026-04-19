   function pos_out=pos_clip(pos_in,wh)
%  function pos_out=pos_clip(pos_in,wh)
%  Constrain figure position to be on screen,
%  useful when switching between bench and notebook PCs.
%  Works only for position definitions in pixels
%  Dick Benson DSP Technology  

   scrn    = get(0,'screensize');
   if scrn(3)~=640
       scrn=scrn+[2,2,-6,-6];
   end;
   if nargin==2
     pos_out = [pos_in(1:2),wh];  % replace width/height with wh
   else
     pos_out = pos_in;
   end;
   
   %  check left side
   pos_out(1) = max(pos_in(1),scrn(1));
   %  check bottom
   pos_out(2) = max(pos_in(2),scrn(2))+1;
   % check right side
   pos_out(1) = min(pos_out(1)+pos_in(3),scrn(1)+scrn(3))-pos_in(3);
   % check top
   
   pos_out(2) = min(pos_out(2)+pos_in(4),scrn(2)+scrn(4)-44)-pos_in(4)+1; 
   pos_out=abs(pos_out); 
% end function 


