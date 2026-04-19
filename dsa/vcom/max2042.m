  function [s1, s2]=max2042(in_s1,in_s2)
% function [s1, s2]=max2042(in_s1,in_s2) 
% expand arrays in vdlg_1 to 16 channels
% Dick Benson DSP Technology
   [r,c]=size(in_s1);
   if r ~=16
      % move to 16 channel storage
      s1=[in_s1;in_s1(14,:);in_s1(14,:)]; 
      s2=[in_s2;'Channel15~Gs~      ';'Channel16~Gs~      '];
   else
      % no change
      s1=in_s1;
      s2=in_s2;
   end;
% end function
